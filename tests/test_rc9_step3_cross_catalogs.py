"""
tests/test_rc9_step3_cross_catalogs.py — 第 3 步跨词库 10 项条目专项验收测试套件

验收目标：
1. 目录规模精确断言：配饰 19、内衣 12、微瑕 8、状态 23、款式 137；
2. 合法 Facts 枚举校验：绝无 neck/head/bottom 等非法枚举，所有新增条目的 SemanticFacts 实例化无警告与异常；
3. 承载资格与具体形制动作兼容性分层验证矩阵：
   - 4 款外穿配饰（hooded_cloak, cloak, poncho, fur_shawl）单穿可承载 wet_pure 等通用修饰；
   - 扣件/拉链/掀裙等形制动作按白名单严格拒绝（unbuttoned, unzipped, lifted_up）；
   - discarded/ambient 配饰注销在穿承载资格；
   - 显式目标不兼容时直接拒绝 (UNBOUND_INCOMPATIBLE)，绝不回退改绑其他衣物；
   - 非承载配饰（neck_ribbon, waist_belt, winter_scarf）单穿拒绝承载；
4. 内衣与缺席状态互斥判定：
   - crotchless_panties 仅互斥 Drop underwearless，与 braless 共存；
   - basic_underwear 作为成套内衣同时与 braless 及 underwearless 互斥 Drop；
5. 微瑕唯一归属与纯净描述：
   - tan_lines 槽位严格为 imperfections，provenance 追溯清晰，无 removed swimwear 等动作词，不污染 clothing/nudity。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import unittest

from lib.conflict_resolver import (
    BindingStatus,
    ConflictResolver,
    GarmentCarrierEntity,
    build_garment_entity_key,
    extract_garment_entities,
    find_bound_carrier,
    is_garment_compatible_with_state,
)
from lib.models import (
    PromptAtom,
    SemanticFacts,
    SpanType,
    TagProvenance,
    VALID_GARMENT_TOPOLOGIES,
    VALID_PROP_USAGES,
    VALID_VISIBLE_REGIONS,
)
from lib.sampler import DataSampler
import nodes

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"


class TestRC9Step3CrossCatalogs(unittest.TestCase):
    """第 3 步跨词库 10 项条目（配饰 7、内衣 2、微瑕 1）专项测试套件"""

    @classmethod
    def setUpClass(cls):
        cls.sampler = DataSampler(DATA_DIR)
        cls.generator = nodes.NODE_CLASS_MAPPINGS["IYKYKPromptGenerator"]()

        # 加载数据源
        cls.accessories_doc = json.loads((DATA_DIR / "accessories.json").read_text(encoding="utf-8"))
        cls.clothing_doc = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        cls.imperfections_doc = json.loads((DATA_DIR / "imperfections.json").read_text(encoding="utf-8"))

    # ─────────────────────────────────────────────────────────────
    # 1. 目录规模指标精确断言
    # ─────────────────────────────────────────────────────────────
    def test_01_catalog_counts(self):
        """验证目录规模：配饰 19、内衣 12、微瑕 8、服装状态 23、服装款式 137"""
        jewelry_items = self.accessories_doc.get("headwear_jewelry", [])
        self.assertEqual(len(jewelry_items), 19, f"Expected 19 jewelry items, got {len(jewelry_items)}")

        lingerie_items = self.clothing_doc.get("lingerie_wardrobe", [])
        self.assertEqual(len(lingerie_items), 12, f"Expected 12 lingerie items, got {len(lingerie_items)}")

        imperfection_items = self.imperfections_doc.get("categories", [])
        self.assertEqual(len(imperfection_items), 8, f"Expected 8 imperfection items, got {len(imperfection_items)}")

        clothing_states = self.clothing_doc.get("clothing_states", [])
        self.assertEqual(len(clothing_states), 23, f"Expected 23 clothing states, got {len(clothing_states)}")

        clothing_categories = self.clothing_doc.get("categories", [])
        self.assertEqual(len(clothing_categories), 137, f"Expected 137 clothing categories, got {len(clothing_categories)}")

        # 验证 10 项新增条目 ID 均存在
        expected_new_jewelry = {"neck_ribbon", "hooded_cloak", "cloak", "poncho", "waist_belt", "winter_scarf", "fur_shawl"}
        actual_jewelry_ids = {j["id"] for j in jewelry_items}
        self.assertTrue(expected_new_jewelry.issubset(actual_jewelry_ids), f"Missing jewelry items: {expected_new_jewelry - actual_jewelry_ids}")

        expected_new_lingerie = {"crotchless_panties", "basic_underwear"}
        actual_lingerie_ids = {lingerie_item["id"] for lingerie_item in lingerie_items}
        self.assertTrue(expected_new_lingerie.issubset(actual_lingerie_ids), f"Missing lingerie items: {expected_new_lingerie - actual_lingerie_ids}")

        actual_imperfection_ids = {i["id"] for i in imperfection_items}
        self.assertIn("tan_lines", actual_imperfection_ids, "Missing tan_lines in imperfections")

    # ─────────────────────────────────────────────────────────────
    # 2. 合法 Facts 枚举校验与非法枚举拒绝
    # ─────────────────────────────────────────────────────────────
    def test_02_facts_enumeration_integrity(self):
        """严格核验 10 项新增条目的 Facts 事实均属于合法枚举，绝无 neck, head, bottom 等非法枚举"""
        items_to_check = []
        for j in self.accessories_doc.get("headwear_jewelry", []):
            if j["id"] in {"neck_ribbon", "hooded_cloak", "cloak", "poncho", "waist_belt", "winter_scarf", "fur_shawl"}:
                items_to_check.append(("jewelry", j))

        for lingerie_item in self.clothing_doc.get("lingerie_wardrobe", []):
            if lingerie_item["id"] in {"crotchless_panties", "basic_underwear"}:
                items_to_check.append(("lingerie", lingerie_item))

        for i in self.imperfections_doc.get("categories", []):
            if i["id"] == "tan_lines":
                items_to_check.append(("imperfections", i))

        self.assertEqual(len(items_to_check), 10, f"Expected 10 items to check, found {len(items_to_check)}")

        for cat, item in items_to_check:
            for tag in item.get("tags", []):
                facts_dict = tag.get("facts", {})
                # 1. 验证 visible_regions
                for vr in facts_dict.get("visible_regions", []):
                    self.assertIn(
                        vr,
                        VALID_VISIBLE_REGIONS,
                        f"Illegal visible_region '{vr}' in item '{item['id']}'! Valid: {VALID_VISIBLE_REGIONS}"
                    )
                    self.assertNotIn(vr, ["neck", "head"], f"Forbidden region '{vr}' in '{item['id']}'")

                # 2. 验证 garment_topologies
                for gt in facts_dict.get("garment_topologies", []):
                    self.assertIn(
                        gt,
                        VALID_GARMENT_TOPOLOGIES,
                        f"Illegal garment_topology '{gt}' in item '{item['id']}'! Valid: {VALID_GARMENT_TOPOLOGIES}"
                    )
                    self.assertNotIn(gt, ["bottom"], f"Forbidden topology 'bottom' in '{item['id']}'")

                # 3. 验证 prop_usage
                pu = facts_dict.get("prop_usage")
                if pu is not None:
                    self.assertIn(
                        pu,
                        VALID_PROP_USAGES,
                        f"Illegal prop_usage '{pu}' in item '{item['id']}'! Valid: {VALID_PROP_USAGES}"
                    )

                # 4. 实例化 SemanticFacts 确保 Fail-Closed 无异常
                try:
                    SemanticFacts(
                        semantic_role=facts_dict.get("semantic_role"),
                        visible_regions=tuple(facts_dict.get("visible_regions", ())),
                        garment_topologies=tuple(facts_dict.get("garment_topologies", ())),
                        garment_states=tuple(facts_dict.get("garment_states", ())),
                        prop_usage=facts_dict.get("prop_usage"),
                    )
                except Exception as e:
                    self.fail(f"SemanticFacts instantiation failed for {item['id']}: {e}")

    # ─────────────────────────────────────────────────────────────
    # 3. 承载资格与形制动作兼容性分层验证矩阵
    # ─────────────────────────────────────────────────────────────
    def test_03_outerwear_accessories_carrier_general_modifier(self):
        """四款外穿配饰（hooded_cloak, cloak, poncho, fur_shawl）单穿时具备承载资格，可成功绑定 wet_pure 修饰"""
        for item_id in ("hooded_cloak", "cloak", "poncho", "fur_shawl"):
            # 构造外穿配饰原子
            acc_atom = PromptAtom(
                atom_id=f"atom_{item_id}",
                text=item_id,
                span_type=SpanType.PLAIN,
                source_slot="jewelry",
                source_item_id=item_id,
                facts=SemanticFacts(
                    semantic_role="selector",
                    prop_usage="worn",
                    garment_topologies=("outerwear",),
                    visible_regions=("upper_body",),
                ),
                provenance=TagProvenance(kind="jewelry", item_id=item_id),
            )
            ekey = build_garment_entity_key(acc_atom)
            self.assertEqual(ekey, f"garment:jewelry:{item_id}", f"Failed to generate entity key for outerwear accessory: {item_id}")

            entities = extract_garment_entities([acc_atom])
            self.assertIn(ekey, entities)
            entity = entities[ekey]
            self.assertTrue(entity.is_worn)
            self.assertFalse(entity.is_ambient)

            # 构造通用服装修饰原子 (wet_pure)
            state_atom = PromptAtom(
                atom_id="atom_wet_pure",
                text="wet clothes",
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id="wet_pure",
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_states=("worn",),
                ),
                provenance=TagProvenance(kind="clothing_state", item_id="wet_pure"),
            )
            binding = find_bound_carrier(state_atom, list(entities.values()))
            self.assertEqual(
                binding.status,
                BindingStatus.BOUND,
                f"Outerwear accessory {item_id} should bind wet_pure, got status: {binding.status} ({binding.reason})"
            )
            self.assertEqual(binding.target_entity.entity_id, ekey)

    def test_04_outerwear_accessories_reject_incompatible_actions(self):
        """四款外穿配饰虽然具备承载主体资格，但严格按形制白名单拒绝不支持的解扣、拉链、掀裙等具体动作"""
        for item_id in ("hooded_cloak", "cloak", "poncho", "fur_shawl"):
            acc_atom = PromptAtom(
                atom_id=f"atom_{item_id}",
                text=item_id,
                span_type=SpanType.PLAIN,
                source_slot="jewelry",
                source_item_id=item_id,
                facts=SemanticFacts(
                    semantic_role="selector",
                    prop_usage="worn",
                    garment_topologies=("outerwear",),
                    visible_regions=("upper_body",),
                ),
                provenance=TagProvenance(kind="jewelry", item_id=item_id),
            )
            entities = list(extract_garment_entities([acc_atom]).values())

            # 1. 拒绝 unbuttoned (不在 ALLOWED_BUTTON_STYLES)
            state_button = PromptAtom(
                atom_id="atom_unbuttoned",
                text="unbuttoned coat",
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id="unbuttoned",
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_topologies=("outerwear",),
                    garment_states=("opened",),
                ),
            )
            res_button = find_bound_carrier(state_button, entities)
            self.assertEqual(
                res_button.status,
                BindingStatus.UNBOUND_NO_CANDIDATE,
                f"{item_id} must reject unbuttoned action due to button capability lack"
            )

            # 2. 拒绝 unzipped (不在 ALLOWED_ZIPPER_STYLES)
            state_zipper = PromptAtom(
                atom_id="atom_unzipped",
                text="unzipped jacket",
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id="unzipped",
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_topologies=("outerwear",),
                    garment_states=("opened",),
                ),
            )
            res_zipper = find_bound_carrier(state_zipper, entities)
            self.assertEqual(
                res_zipper.status,
                BindingStatus.UNBOUND_NO_CANDIDATE,
                f"{item_id} must reject unzipped action due to zipper capability lack"
            )

            # 3. 拒绝 lifted_up (无 skirt / one_piece 拓扑)
            state_lifted = PromptAtom(
                atom_id="atom_lifted_up",
                text="skirt lifted up",
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id="lifted_up",
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_topologies=("bottom_skirt",),
                    garment_states=("lifted",),
                ),
            )
            res_lifted = find_bound_carrier(state_lifted, entities)
            self.assertEqual(
                res_lifted.status,
                BindingStatus.UNBOUND_NO_CANDIDATE,
                f"{item_id} must reject lifted_up action due to skirt topology lack"
            )

    def test_05_discarded_or_ambient_accessory_loses_carrier_eligibility(self):
        """当外穿配饰处于 discarded 状态或被标记为环境物时，丧失在穿承载主体资格"""
        acc_atom = PromptAtom(
            atom_id="atom_cloak",
            text="cloak",
            span_type=SpanType.PLAIN,
            source_slot="jewelry",
            source_item_id="cloak",
            facts=SemanticFacts(
                semantic_role="selector",
                prop_usage="worn",
                garment_topologies=("outerwear",),
                visible_regions=("upper_body",),
            ),
            provenance=TagProvenance(kind="jewelry", item_id="cloak"),
        )
        entities = extract_garment_entities([acc_atom])
        entity = entities["garment:jewelry:cloak"]

        # 模拟 discarded 或 ambient
        entity.is_worn = False
        entity.is_ambient = True

        state_atom = PromptAtom(
            atom_id="atom_wet_pure",
            text="wet clothes",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="wet_pure",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("worn",),
            ),
        )
        binding = find_bound_carrier(state_atom, [entity])
        self.assertEqual(
            binding.status,
            BindingStatus.UNBOUND_NO_CANDIDATE,
            "Discarded / ambient accessory must lose worn carrier eligibility"
        )

    def test_06_explicit_target_incompatible_rejects_without_fallback(self):
        """显式目标不兼容时直接返回 UNBOUND_INCOMPATIBLE，绝不回退改绑其他合法衣物"""
        suit_atom = PromptAtom(
            atom_id="atom_suit",
            text="business suit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="business_suit",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_topologies=("top",),
                visible_regions=("upper_body",),
            ),
            provenance=TagProvenance(kind="clothing", item_id="business_suit"),
        )
        cloak_atom = PromptAtom(
            atom_id="atom_cloak",
            text="cloak",
            span_type=SpanType.PLAIN,
            source_slot="jewelry",
            source_item_id="cloak",
            facts=SemanticFacts(
                semantic_role="selector",
                prop_usage="worn",
                garment_topologies=("outerwear",),
                visible_regions=("upper_body",),
            ),
            provenance=TagProvenance(kind="jewelry", item_id="cloak"),
        )
        entities = list(extract_garment_entities([suit_atom, cloak_atom]).values())
        self.assertEqual(len(entities), 2)

        # 状态 unbuttoned 显式指定 target_id="cloak"
        state_atom = PromptAtom(
            atom_id="atom_unbuttoned",
            text="unbuttoned cloak",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            target_id="cloak",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("opened",),
            ),
        )
        binding = find_bound_carrier(state_atom, entities, target_id="cloak")
        self.assertEqual(
            binding.status,
            BindingStatus.UNBOUND_INCOMPATIBLE,
            "Explicit target incompatible must fail with UNBOUND_INCOMPATIBLE"
        )
        self.assertEqual(binding.target_entity.selected_id, "cloak")
        # 绝不能改绑到 business_suit 上！
        self.assertNotEqual(
            binding.target_entity.selected_id,
            "business_suit",
            "Must NOT fallback to business_suit when cloak was explicitly targeted!"
        )

    def test_07_non_carrier_accessories_reject_carrying_alone(self):
        """非承载配饰（neck_ribbon, waist_belt, winter_scarf）单穿时不具备承载资格"""
        for item_id in ("neck_ribbon", "waist_belt", "winter_scarf"):
            acc_atom = PromptAtom(
                atom_id=f"atom_{item_id}",
                text=item_id,
                span_type=SpanType.PLAIN,
                source_slot="jewelry",
                source_item_id=item_id,
                facts=SemanticFacts(
                    semantic_role="selector",
                    prop_usage="worn",
                    visible_regions=("upper_body",),
                ),
                provenance=TagProvenance(kind="jewelry", item_id=item_id),
            )
            ekey = build_garment_entity_key(acc_atom)
            self.assertIsNone(ekey, f"Non-carrier accessory {item_id} must NOT generate a garment entity key")

            entities = extract_garment_entities([acc_atom])
            self.assertEqual(len(entities), 0, f"Non-carrier accessory {item_id} must not produce any entities")

            state_atom = PromptAtom(
                atom_id="atom_wet_pure",
                text="wet clothes",
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id="wet_pure",
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_states=("worn",),
                ),
            )
            binding = find_bound_carrier(state_atom, list(entities.values()))
            self.assertEqual(
                binding.status,
                BindingStatus.UNBOUND_NO_CANDIDATE,
                f"Non-carrier accessory {item_id} alone must result in UNBOUND_NO_CANDIDATE"
            )

    # ─────────────────────────────────────────────────────────────
    # 4. 内衣与缺席状态互斥判定契约
    # ─────────────────────────────────────────────────────────────
    def test_08_crotchless_panties_absence_mutual_exclusion(self):
        """crotchless_panties 互斥 Drop underwearless，但与 braless 共存不互斥"""
        resolver = ConflictResolver(data_dir=DATA_DIR)

        panties_atom = PromptAtom(
            atom_id="atom_crotchless",
            text="crotchless panties",
            span_type=SpanType.PLAIN,
            source_slot="lingerie",
            source_item_id="crotchless_panties",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_topologies=("underwear",),
                visible_regions=("lower_body",),
            ),
            provenance=TagProvenance(kind="lingerie", item_id="crotchless_panties", source_mode="explicit"),
        )
        underless_atom = PromptAtom(
            atom_id="atom_underless",
            text="no underwear, bottomless",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="underwearless",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("removed",),
            ),
            provenance=TagProvenance(kind="clothing_state", item_id="underwearless", source_mode="explicit"),
        )
        braless_atom = PromptAtom(
            atom_id="atom_braless",
            text="braless",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="braless",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("removed",),
            ),
            provenance=TagProvenance(kind="clothing_state", item_id="braless", source_mode="explicit"),
        )

        # 运行消解
        resolved_atoms, rules, report = resolver.resolve_atoms_with_full_report([panties_atom, underless_atom, braless_atom])
        surviving_ids = {a.source_item_id for a in resolved_atoms}

        # underwearless 必须被 drop
        self.assertNotIn("underwearless", surviving_ids, "crotchless_panties must drop underwearless")
        # braless 必须存活
        self.assertIn("braless", surviving_ids, "crotchless_panties must coexist with braless")
        # crotchless_panties 存活
        self.assertIn("crotchless_panties", surviving_ids)

        # 检查决策日志
        drop_decs = [d for d in report.decisions if d.action == "drop" and d.target_atom_id == "atom_underless"]
        self.assertEqual(len(drop_decs), 1, "Expected exactly 1 drop decision for underwearless")
        self.assertEqual(drop_decs[0].reason_code, "absence_state_conflict")

    def test_09_basic_underwear_absence_mutual_exclusion(self):
        """basic_underwear 作为成套内衣同时与 braless 及 underwearless 互斥并裁决 Drop 两者"""
        resolver = ConflictResolver(data_dir=DATA_DIR)

        basic_atom = PromptAtom(
            atom_id="atom_basic_under",
            text="cotton bra and panties set",
            span_type=SpanType.PLAIN,
            source_slot="lingerie",
            source_item_id="basic_underwear",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_topologies=("underwear",),
                visible_regions=("lower_body", "upper_body"),
            ),
            provenance=TagProvenance(kind="lingerie", item_id="basic_underwear", source_mode="explicit"),
        )
        underless_atom = PromptAtom(
            atom_id="atom_underless",
            text="no underwear, bottomless",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="underwearless",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("removed",),
            ),
            provenance=TagProvenance(kind="clothing_state", item_id="underwearless", source_mode="explicit"),
        )
        braless_atom = PromptAtom(
            atom_id="atom_braless",
            text="braless",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="braless",
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("removed",),
            ),
            provenance=TagProvenance(kind="clothing_state", item_id="braless", source_mode="explicit"),
        )

        resolved_atoms, rules, report = resolver.resolve_atoms_with_full_report([basic_atom, underless_atom, braless_atom])
        surviving_ids = {a.source_item_id for a in resolved_atoms}

        # basic_underwear 存活
        self.assertIn("basic_underwear", surviving_ids)
        # braless 与 underwearless 均被 drop
        self.assertNotIn("braless", surviving_ids, "basic_underwear must drop braless")
        self.assertNotIn("underwearless", surviving_ids, "basic_underwear must drop underwearless")

        drop_targets = {d.target_atom_id for d in report.decisions if d.action == "drop"}
        self.assertIn("atom_braless", drop_targets, "atom_braless must be in drop targets")
        self.assertIn("atom_underless", drop_targets, "atom_underless must be in drop targets")

    # ─────────────────────────────────────────────────────────────
    # 5. 微瑕唯一归属与纯净描述
    # ─────────────────────────────────────────────────────────────
    def test_10_tan_lines_provenance_and_clean_description(self):
        """tan_lines 槽位严格为 imperfections，纯皮肤色差描述，绝不引入 removed swimwear 动作词，不污染服装"""
        rng = Random(42)
        tags = self.sampler.sample_imperfections("☀️ 日晒比基尼泳痕 (Bikini Tan Lines)", rng)
        self.assertTrue(len(tags) > 0, "tan_lines returned empty tags")

        # 检查文本纯净性：绝无 'removed' 或 'swimwear'
        for t in tags:
            t_low = t.lower()
            self.assertNotIn("removed", t_low, f"tan_lines tag '{t}' must not contain 'removed'")
            self.assertNotIn("swimwear", t_low, f"tan_lines tag '{t}' must not contain 'swimwear'")

        # 端到端节点生成检查
        r = self.generator.generate_structured(
            预设模板="无 (None)",
            风格配方="无 (None)",
            场景大类="无 (None)",
            剧情主题="无 (None)",
            景别构图="近景 CU (面部表情/眼神)",
            拍摄视角="平视 (自然视线·中立)",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="旗袍 (Qipao/Cheongsam)",
            服装状态="无 (None)",
            发型发色="无 (None)",
            饰品头饰="无 (None)",
            妆容细节="无 (None)",
            姿势动作="无 (None)",
            情绪表情="无 (None)",
            光影预设="自动 (Auto)",
            胶片风格="无 (None)",
            液体效果="无 (None)",
            纹身标记="无 (None)",
            道具物件="无 (None)",
            角色设定="无 (None)",
            真实微瑕="☀️ 日晒比基尼泳痕 (Bikini Tan Lines)",
            画质等级="高清写真 (High)",
            prompt_seed=42,
        )
        tan_atoms = [a for a in r.atoms if a.source_item_id == "tan_lines"]
        self.assertTrue(len(tan_atoms) > 0, "tan_lines atom missing in end-to-end generation")
        for a in tan_atoms:
            self.assertEqual(a.source_slot, "imperfections")
            self.assertIsNotNone(a.provenance)
            self.assertEqual(a.provenance.kind, "imperfections")
            self.assertEqual(a.provenance.item_id, "tan_lines")

    # ─────────────────────────────────────────────────────────────
    # 6. 审计器严格门禁与反例矩阵核验
    # ─────────────────────────────────────────────────────────────
    def test_11_audit_engine_counterexample_matrix(self):
        """核验第 3 步审计器反例拦截矩阵：叶子词条篡改、静默丢弃、多原子首项突变、重复ID、乱序均严格拦截，合法种子放行"""
        import copy
        from scratch.audit_step3_cross_catalog import (
            attribute_step3_seed_diff,
            load_authoritative_catalog_leaf_tags,
        )

        catalog_leaf_tags = load_authoritative_catalog_leaf_tags(DATA_DIR)

        # 构造基线与当前版本的标准测试种子结构
        inputs = {
            "预设模板": "无 (None)", "风格配方": "无 (None)", "场景大类": "随机 (Random)", "剧情主题": "随机 (Random)",
            "景别构图": "自动 (Auto)", "拍摄视角": "自动 (Auto)", "裸露等级": "随机 (Random)", "服装款式": "随机 (Random)",
            "服装状态": "自动联动裸露等级 (Auto Link Nudity)", "发型发色": "随机 (Random)", "饰品头饰": "随机 (Random)",
            "妆容细节": "随机 (Random)", "姿势动作": "随机 (Random)", "情绪表情": "随机 (Random)", "光影预设": "自动 (Auto)",
            "胶片风格": "随机 (Random)", "液体效果": "随机 (Random)", "纹身标记": "随机 (Random)", "道具物件": "随机 (Random)",
            "角色设定": "随机 (Random)", "真实微瑕": "随机 (Random)", "画质等级": "高清写真 (High)",
        }
        r = self.generator.generate_structured(**inputs, prompt_seed=42)

        def atom_to_dict(a):
            return {
                "atom_id": a.atom_id,
                "id": a.id,
                "text": a.text,
                "source_slot": a.source_slot,
                "source_item_id": a.source_item_id or (a.provenance.item_id if a.provenance else "") or "",
                "tag_order": a.tag_order,
                "span_order": a.span_order,
                "garment_topologies": list(a.facts.garment_topologies) if a.facts else [],
                "garment_states": list(a.facts.garment_states) if a.facts else [],
                "visible_regions": list(a.facts.visible_regions) if a.facts else [],
                "prop_usage": a.facts.prop_usage if a.facts else None,
            }

        def dec_to_dict(d):
            return {
                "decision_id": d.decision_id,
                "rule_id": d.rule_id,
                "reason_code": d.reason_code,
                "action": d.action,
                "target_atom_id": d.target_atom_id,
                "winner_atom_ids": list(d.winner_atom_ids),
                "produced_atom_ids": list(d.produced_atom_ids),
                "before_text": d.before_text,
                "after_text": d.after_text,
            }

        base_item = {
            "positive": r.positive,
            "hash": "base_hash_42",
            "source_atoms": [atom_to_dict(a) for a in r.source_atoms],
            "final_atoms": [atom_to_dict(a) for a in r.atoms],
            "decisions": [dec_to_dict(d) for d in (r.resolution_report.decisions if r.resolution_report else ())],
            "carrier_bindings": {},
            "dedup_records": [{"atom_id": rec.atom_id, "retained_atom_id": rec.retained_atom_id, "basis": rec.basis} for rec in (r.deduplication_records or ())],
            "budget_records": [{"atom_id": rec.atom_id, "reason": rec.reason} for rec in (r.budget_filter_records or ())],
        }

        # 1. 正向验证：合法结构 100% 通过且零未解释原因
        cur_legit = copy.deepcopy(base_item)
        res_legit = attribute_step3_seed_diff(42, base_item, cur_legit, catalog_leaf_tags)
        self.assertFalse(res_legit["is_unexplained"], f"Legit seed should not be unexplained: {res_legit['unexplained_reasons']}")
        self.assertEqual(len(res_legit["unexplained_reasons"]), 0)

        # 2. 反例 A：合法条目 ID (neck_ribbon) 伪造夹带无关叶子词条 (invented unrelated token)
        cur_tampered_leaf = copy.deepcopy(base_item)
        tampered_atom = {
            "atom_id": "atom_tampered_neck_ribbon",
            "text": "invented unrelated token",
            "source_slot": "jewelry",
            "source_item_id": "neck_ribbon",
            "tag_order": 99,
            "span_order": 0,
        }
        cur_tampered_leaf["source_atoms"].append(tampered_atom)
        cur_tampered_leaf["final_atoms"].append(tampered_atom)
        res_tamper = attribute_step3_seed_diff(42, base_item, cur_tampered_leaf, catalog_leaf_tags)
        self.assertTrue(res_tamper["is_unexplained"], "Tampered leaf tag under valid ID must be rejected")
        self.assertTrue(
            any("UNEXPLAINED_TAMPERED_CATALOG_TAG(jewelry)" in r for r in res_tamper["unexplained_reasons"]),
            f"Expected UNEXPLAINED_TAMPERED_CATALOG_TAG(jewelry), got: {res_tamper['unexplained_reasons']}"
        )

        # 3. 反例 B：来源原子保留、终态无决策静默丢失 (Silent Atom Drop)
        cur_silent_drop = copy.deepcopy(base_item)
        dropped_target = cur_silent_drop["source_atoms"][0]
        cur_silent_drop["final_atoms"] = [a for a in cur_silent_drop["final_atoms"] if a["atom_id"] != dropped_target["atom_id"]]
        res_silent = attribute_step3_seed_diff(42, base_item, cur_silent_drop, catalog_leaf_tags)
        self.assertTrue(res_silent["is_unexplained"], "Silent atom drop without drop decision must be rejected")
        self.assertTrue(
            any("UNEXPLAINED_SILENT_ATOM_DROP" in r for r in res_silent["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SILENT_ATOM_DROP, got: {res_silent['unexplained_reasons']}"
        )

        # 4. 反例 C：多原子槽位首项篡改（未变更槽位 scene_theme 包含多个原子，篡改第一个、保留后续）
        cur_multi_mutation = copy.deepcopy(base_item)
        scene_atoms = [a for a in cur_multi_mutation["source_atoms"] if a["source_slot"] == "scene_theme"]
        self.assertGreaterEqual(len(scene_atoms), 2, "Test seed must have >= 2 scene_theme atoms for multi-atom test")
        scene_atoms[0]["text"] = "tampered first scene tag"
        res_multi = attribute_step3_seed_diff(42, base_item, cur_multi_mutation, catalog_leaf_tags)
        self.assertTrue(res_multi["is_unexplained"], "First atom mutation in multi-atom slot must be rejected")
        self.assertTrue(
            any("UNEXPLAINED_UNEXPECTED_SLOT_MUTATION(scene_theme)" in r for r in res_multi["unexplained_reasons"]),
            f"Expected UNEXPLAINED_UNEXPECTED_SLOT_MUTATION(scene_theme), got: {res_multi['unexplained_reasons']}"
        )

        # 5. 反例 D：重复原子 ID 注入
        cur_dup_id = copy.deepcopy(base_item)
        cur_dup_id["final_atoms"].append(cur_dup_id["final_atoms"][0])
        res_dup = attribute_step3_seed_diff(42, base_item, cur_dup_id, catalog_leaf_tags)
        self.assertTrue(res_dup["is_unexplained"], "Duplicate atom ID must be rejected")
        self.assertTrue(
            any("DUPLICATE_ATOM_ID" in r for r in res_dup["unexplained_reasons"]),
            f"Expected DUPLICATE_ATOM_ID, got: {res_dup['unexplained_reasons']}"
        )

        # 6. 反例 E：相对序列乱序 (Sequence Order Drift)
        cur_order_drift = copy.deepcopy(base_item)
        unchanged_indices = [
            i for i, a in enumerate(cur_order_drift["final_atoms"])
            if a["source_slot"] not in ("jewelry", "imperfections", "clothing", "clothing_state", "clothing_extension", "linkage")
        ]
        self.assertGreaterEqual(len(unchanged_indices), 2, "Test seed must have >= 2 unchanged atoms")
        i1, i2 = unchanged_indices[0], unchanged_indices[1]
        cur_order_drift["final_atoms"][i1], cur_order_drift["final_atoms"][i2] = (
            cur_order_drift["final_atoms"][i2],
            cur_order_drift["final_atoms"][i1],
        )
        res_drift = attribute_step3_seed_diff(42, base_item, cur_order_drift, catalog_leaf_tags)
        self.assertTrue(res_drift["is_unexplained"], "Sequence order drift in unchanged slots must be rejected")
        self.assertTrue(
            any("UNEXPLAINED_SEQUENCE_ORDER_MUTATION" in r or "UNEXPLAINED_TAG_ORDER_VIOLATION" in r for r in res_drift["unexplained_reasons"]),
            f"Expected SEQUENCE_ORDER or TAG_ORDER violation, got: {res_drift['unexplained_reasons']}"
        )

        # 7. 反例 F (用户复现 1)：新增合法 neck_ribbon 并伪造 absence_state_conflict 裁决删除构图原子 (full body shot)
        cur_tampered_absence = copy.deepcopy(base_item)
        acc_atom = {
            "atom_id": "atom_legit_neck_ribbon",
            "text": "neck ribbon",
            "source_slot": "jewelry",
            "source_item_id": "neck_ribbon",
            "tag_order": 90,
            "span_order": 0,
        }
        cur_tampered_absence["source_atoms"].append(acc_atom)
        cur_tampered_absence["final_atoms"].append(acc_atom)
        framing_atom = next(
            (a for a in cur_tampered_absence["source_atoms"] if a["source_slot"] in ("shot_type", "composition")),
            None
        )
        self.assertIsNotNone(framing_atom, "Test seed must have shot_type / composition atom for Counterexample F")
        cur_tampered_absence["final_atoms"] = [
            a for a in cur_tampered_absence["final_atoms"] if a["atom_id"] != framing_atom["atom_id"]
        ]
        malicious_absence_dec = {
            "decision_id": "dec_tampered_absence_conflict",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": framing_atom["atom_id"],
            "winner_atom_ids": ["atom_legit_neck_ribbon"],
            "produced_atom_ids": [],
            "before_text": framing_atom["text"],
            "after_text": "",
        }
        cur_tampered_absence["decisions"].append(malicious_absence_dec)
        res_tampered_absence = attribute_step3_seed_diff(42, base_item, cur_tampered_absence, catalog_leaf_tags)
        self.assertTrue(
            res_tampered_absence["is_unexplained"],
            "Dropping composition atom via absence_state_conflict with jewelry winner must be strictly rejected"
        )
        self.assertTrue(
            any("ILLEGAL_ABSENCE_CONFLICT_TARGET" in r for r in res_tampered_absence["unexplained_reasons"]),
            f"Expected ILLEGAL_ABSENCE_CONFLICT_TARGET, got: {res_tampered_absence['unexplained_reasons']}"
        )
        self.assertTrue(
            any("ILLEGAL_ABSENCE_CONFLICT_WINNER" in r for r in res_tampered_absence["unexplained_reasons"]),
            f"Expected ILLEGAL_ABSENCE_CONFLICT_WINNER, got: {res_tampered_absence['unexplained_reasons']}"
        )

        # 8. 反例 G (用户复现 2)：保持来源原子合法，终态篡改 source_item_id 为 invented_style 且 tag_order 为 999
        cur_tampered_sig = copy.deepcopy(base_item)
        target_final = cur_tampered_sig["final_atoms"][0]
        target_final["source_item_id"] = "invented_style"
        target_final["tag_order"] = 999
        res_tampered_sig = attribute_step3_seed_diff(42, base_item, cur_tampered_sig, catalog_leaf_tags)
        self.assertTrue(
            res_tampered_sig["is_unexplained"],
            "Tampered final atom source_item_id and tag_order must be strictly rejected"
        )
        self.assertTrue(
            any("UNEXPLAINED_FIELD_DRIFT(source_item_id)" in r for r in res_tampered_sig["unexplained_reasons"]),
            f"Expected UNEXPLAINED_FIELD_DRIFT(source_item_id), got: {res_tampered_sig['unexplained_reasons']}"
        )
        self.assertTrue(
            any("UNEXPLAINED_ORDER_FIELD_DRIFT(tag_order)" in r or "UNEXPLAINED_TAG_ORDER_VIOLATION" in r for r in res_tampered_sig["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ORDER_FIELD_DRIFT(tag_order) or TAG_ORDER_VIOLATION, got: {res_tampered_sig['unexplained_reasons']}"
        )

        # 9. 反例 H (Defect 3)：跨缺席状态冒领归因拦截 (Cross-claiming absence decision)
        # 基线中存在 braless 状态原子，且在终态中缺失；当前版本提供了一条合法的 underwearless 冲突裁决，尝试跨状态冒领证明 braless 的删除
        cur_cross_claim = copy.deepcopy(base_item)
        base_with_bra = copy.deepcopy(base_item)
        bra_atom = {
            "atom_id": "atom_base_braless",
            "text": "braless",
            "source_slot": "clothing_state",
            "source_item_id": "braless",
            "garment_states": ["removed"],
            "tag_order": 50,
            "span_order": 0,
        }
        panties_atom = {
            "atom_id": "atom_cur_panties",
            "text": "crotchless panties",
            "source_slot": "clothing",
            "source_item_id": "crotchless_panties",
            "garment_topologies": ["underwear", "bottom"],
            "tag_order": 60,
            "span_order": 0,
        }
        underwearless_atom = {
            "atom_id": "atom_cur_underwearless",
            "text": "underwearless",
            "source_slot": "clothing_state",
            "source_item_id": "underwearless",
            "garment_states": ["removed"],
            "tag_order": 55,
            "span_order": 0,
        }
        base_with_bra["final_atoms"].append(bra_atom)
        base_with_bra["source_atoms"].append(bra_atom)
        # 当前版本中 braless 缺失，但提供了一项针对 underwearless 的决策
        cur_cross_claim["source_atoms"].extend([panties_atom, underwearless_atom])
        cur_cross_claim["final_atoms"].append(panties_atom)
        cur_cross_claim["decisions"].append({
            "decision_id": "dec_panties_underwearless",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_cur_underwearless",
            "winner_atom_ids": ["atom_cur_panties"],
            "produced_atom_ids": [],
            "before_text": "underwearless",
            "after_text": "",
        })
        res_cross_claim = attribute_step3_seed_diff(42, base_with_bra, cur_cross_claim, catalog_leaf_tags)
        self.assertTrue(
            res_cross_claim["is_unexplained"],
            "Cross-claiming braless drop via underwearless conflict decision must be rejected"
        )
        self.assertTrue(
            any("UNEXPLAINED_ABSENCE_DROP" in r for r in res_cross_claim["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ABSENCE_DROP, got: {res_cross_claim['unexplained_reasons']}"
        )

        # 10. 反例 I (Defect 3)：一裁多销拦截 (Double-claiming: 两个被丢弃的缺席原子仅能核销一次)
        base_with_two_absences = copy.deepcopy(base_item)
        bra_atom_1 = dict(bra_atom, atom_id="atom_bra_1")
        bra_atom_2 = dict(bra_atom, atom_id="atom_bra_2")
        base_with_two_absences["final_atoms"].extend([bra_atom_1, bra_atom_2])
        base_with_two_absences["source_atoms"].extend([bra_atom_1, bra_atom_2])

        cur_double_claim = copy.deepcopy(base_item)
        basic_bra = {
            "atom_id": "atom_cur_basic_bra",
            "text": "basic underwear",
            "source_slot": "clothing",
            "source_item_id": "basic_underwear",
            "garment_topologies": ["underwear", "top", "bottom"],
            "tag_order": 65,
            "span_order": 0,
        }
        cur_double_claim["source_atoms"].extend([bra_atom_1, basic_bra])
        cur_double_claim["final_atoms"].append(basic_bra)
        # 仅有一条针对 bra_atom_1 的合法裁决，bra_atom_2 无法获得独立裁决核销
        cur_double_claim["decisions"].append({
            "decision_id": "dec_bra_single",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_bra_1",
            "winner_atom_ids": ["atom_cur_basic_bra"],
            "produced_atom_ids": [],
            "before_text": "braless",
            "after_text": "",
        })
        res_double_claim = attribute_step3_seed_diff(42, base_with_two_absences, cur_double_claim, catalog_leaf_tags)
        self.assertTrue(
            res_double_claim["is_unexplained"],
            "Double-claiming a single absence conflict decision for multiple dropped atoms must be rejected"
        )
        self.assertTrue(
            any("UNEXPLAINED_ABSENCE_DROP" in r for r in res_double_claim["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ABSENCE_DROP for second atom, got: {res_double_claim['unexplained_reasons']}"
        )

    # ─────────────────────────────────────────────────────────────
    # 7. 统一来源签名核验入口专门测试 (Unified Source Signature Verifier)
    # ─────────────────────────────────────────────────────────────
    def test_12_unified_source_signature_verifier(self):
        """验证统一来源签名核验入口 verify_atom_source_signature：对所有字段漂移严格 Fail-Closed"""
        from scratch.audit_step3_cross_catalog import verify_atom_source_signature

        src = {
            "atom_id": "atom_sample_001",
            "text": "silk qipao",
            "source_slot": "clothing",
            "source_item_id": "qipao",
            "tag_order": 5,
            "span_order": 0,
        }

        # 1. 签名完全吻合 -> 0 错误
        self.assertEqual(verify_atom_source_signature(src, src), [])

        # 2. atom_id 漂移
        bad_id = dict(src, atom_id="atom_tampered_id")
        errs = verify_atom_source_signature(bad_id, src)
        self.assertTrue(any("UNEXPLAINED_FIELD_DRIFT(atom_id)" in e for e in errs), f"got: {errs}")

        # 3. text 漂移
        bad_txt = dict(src, text="invented unrelated token")
        errs = verify_atom_source_signature(bad_txt, src)
        self.assertTrue(any("UNEXPLAINED_TAMPERED_RESTORED_ATOM(text)" in e for e in errs), f"got: {errs}")

        # 4. source_slot 漂移
        bad_slot = dict(src, source_slot="jewelry")
        errs = verify_atom_source_signature(bad_slot, src)
        self.assertTrue(any("UNEXPLAINED_FIELD_DRIFT(source_slot)" in e for e in errs), f"got: {errs}")

        # 5. source_item_id 漂移
        bad_item = dict(src, source_item_id="invented_style")
        errs = verify_atom_source_signature(bad_item, src)
        self.assertTrue(any("UNEXPLAINED_FIELD_DRIFT(source_item_id)" in e for e in errs), f"got: {errs}")

        # 6. tag_order 漂移
        bad_tag_order = dict(src, tag_order=999)
        errs = verify_atom_source_signature(bad_tag_order, src)
        self.assertTrue(any("UNEXPLAINED_ORDER_FIELD_DRIFT(tag_order)" in e for e in errs), f"got: {errs}")

        # 7. span_order 漂移
        bad_span_order = dict(src, span_order=9)
        errs = verify_atom_source_signature(bad_span_order, src)
        self.assertTrue(any("UNEXPLAINED_ORDER_FIELD_DRIFT(span_order)" in e for e in errs), f"got: {errs}")

    # ─────────────────────────────────────────────────────────────
    # 8. 统一决策合法性核验入口专门测试 (Unified Decision Legality Verifier)
    # ─────────────────────────────────────────────────────────────
    def test_13_unified_decision_legality_verifier(self):
        """验证统一决策合法性核验入口 verify_decision_legality：覆盖用户指出的 4 项非法决策反例及载体合法性"""
        from scratch.audit_step3_cross_catalog import (
            load_authoritative_resolver_rules,
            verify_decision_legality,
            replay_and_verify_decisions,
        )

        valid_rules = load_authoritative_resolver_rules(DATA_DIR)

        cur_src_map = {
            "atom_braless": {
                "atom_id": "atom_braless",
                "text": "braless",
                "source_slot": "clothing_state",
                "source_item_id": "braless",
                "garment_states": ["removed"],
                "is_worn": True,
            },
            "atom_underwearless": {
                "atom_id": "atom_underwearless",
                "text": "underwearless",
                "source_slot": "clothing_state",
                "source_item_id": "underwearless",
                "garment_states": ["removed"],
                "is_worn": True,
            },
            "atom_panties": {
                "atom_id": "atom_panties",
                "text": "crotchless panties",
                "source_slot": "clothing",
                "source_item_id": "crotchless_panties",
                "garment_topologies": ["underwear", "bottom"],
                "is_worn": True,
            },
            "atom_basic_underwear": {
                "atom_id": "atom_basic_underwear",
                "text": "basic underwear",
                "source_slot": "clothing",
                "source_item_id": "basic_underwear",
                "garment_topologies": ["underwear", "top", "bottom"],
                "is_worn": True,
            },
            "atom_dress": {
                "atom_id": "atom_dress",
                "text": "silk dress",
                "source_slot": "clothing",
                "source_item_id": "dress",
                "garment_topologies": ["one_piece"],
                "is_worn": True,
            },
            "atom_discarded_underwear": {
                "atom_id": "atom_discarded_underwear",
                "text": "basic underwear discarded",
                "source_slot": "clothing",
                "source_item_id": "basic_underwear",
                "garment_topologies": ["underwear", "top", "bottom"],
                "garment_states": ["discarded"],
                "prop_usage": "discarded",
                "is_worn": False,
            },
            "atom_framing": {
                "atom_id": "atom_framing",
                "text": "full body shot",
                "source_slot": "shot_type",
                "source_item_id": "full_body_shot",
                "garment_states": [],
                "garment_topologies": [],
            },
            "atom_neck_ribbon": {
                "atom_id": "atom_neck_ribbon",
                "text": "neck ribbon",
                "source_slot": "jewelry",
                "source_item_id": "neck_ribbon",
                "garment_states": [],
                "garment_topologies": [],
            },
            "atom_wet_state": {
                "atom_id": "atom_wet_state",
                "text": "wet clothes",
                "source_slot": "clothing_state",
                "source_item_id": "wet_pure",
                "garment_states": ["wet_pure"],
            },
        }

        # 1. 合法缺席互斥决策：underwearless 被在穿 panties drop -> 0 错误
        legit_panties_dec = {
            "decision_id": "dec_001",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_underwearless",
            "winner_atom_ids": ["atom_panties"],
            "produced_atom_ids": [],
            "before_text": "underwearless",
            "after_text": "",
        }
        self.assertEqual(verify_decision_legality(legit_panties_dec, cur_src_map, valid_rules), [])

        # 合法缺席互斥决策：braless 被在穿 basic_underwear drop -> 0 错误
        legit_bra_dec = {
            "decision_id": "dec_002",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_braless",
            "winner_atom_ids": ["atom_basic_underwear"],
            "produced_atom_ids": [],
            "before_text": "braless",
            "after_text": "",
        }
        self.assertEqual(verify_decision_legality(legit_bra_dec, cur_src_map, valid_rules), [])

        # 2. 用户复现反例 1：普通连衣裙作为 winner，删除 braless（外装不能证明胸罩在穿） -> 拦截 ILLEGAL_ABSENCE_CONFLICT_WINNER
        bad_dress_winner_dec = {
            "decision_id": "dec_bad_dress",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_braless",
            "winner_atom_ids": ["atom_dress"],
            "produced_atom_ids": [],
            "before_text": "braless",
            "after_text": "",
        }
        errs = verify_decision_legality(bad_dress_winner_dec, cur_src_map, valid_rules)
        self.assertTrue(
            any("ILLEGAL_ABSENCE_CONFLICT_WINNER" in e for e in errs),
            f"Expected ILLEGAL_ABSENCE_CONFLICT_WINNER for dress dropping braless, got: {errs}"
        )

        # 3. 用户复现反例 2：crotchless_panties 作为 winner 删除 braless（下身内裤不与无胸罩冲突） -> 拦截 ILLEGAL_ABSENCE_CONFLICT_WINNER
        bad_panties_drop_bra_dec = {
            "decision_id": "dec_bad_panties",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_braless",
            "winner_atom_ids": ["atom_panties"],
            "produced_atom_ids": [],
            "before_text": "braless",
            "after_text": "",
        }
        errs = verify_decision_legality(bad_panties_drop_bra_dec, cur_src_map, valid_rules)
        self.assertTrue(
            any("ILLEGAL_ABSENCE_CONFLICT_WINNER" in e for e in errs),
            f"Expected ILLEGAL_ABSENCE_CONFLICT_WINNER for panties dropping braless, got: {errs}"
        )

        # 4. 用户复现反例 3：已 discarded 的 basic_underwear 删除缺席状态（非在穿内衣不应触发互斥） -> 拦截 ILLEGAL_ABSENCE_CONFLICT_WINNER_NOT_WORN
        bad_discarded_dec = {
            "decision_id": "dec_bad_discarded",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_braless",
            "winner_atom_ids": ["atom_discarded_underwear"],
            "produced_atom_ids": [],
            "before_text": "braless",
            "after_text": "",
        }
        errs = verify_decision_legality(bad_discarded_dec, cur_src_map, valid_rules)
        self.assertTrue(
            any("ILLEGAL_ABSENCE_CONFLICT_WINNER_NOT_WORN" in e for e in errs),
            f"Expected ILLEGAL_ABSENCE_CONFLICT_WINNER_NOT_WORN for discarded underwear, got: {errs}"
        )

        # 5. 用户复现反例 4：以 state_lacks_carrier 删除构图原子 full body shot -> 拦截 ILLEGAL_CARRIER_BINDING_TARGET
        bad_carrier_framing_dec = {
            "decision_id": "dec_bad_carrier",
            "rule_id": "state_lacks_carrier",
            "reason_code": "state_lacks_carrier",
            "action": "drop",
            "target_atom_id": "atom_framing",
            "winner_atom_ids": [],
            "produced_atom_ids": [],
            "before_text": "full body shot",
            "after_text": "",
        }
        errs = verify_decision_legality(bad_carrier_framing_dec, cur_src_map, valid_rules)
        self.assertTrue(
            any("ILLEGAL_CARRIER_BINDING_TARGET" in e for e in errs),
            f"Expected ILLEGAL_CARRIER_BINDING_TARGET for framing atom, got: {errs}"
        )

        # 6. 饰品 (neck_ribbon) 充当内衣互斥 winner -> 拦截 ILLEGAL_ABSENCE_CONFLICT_WINNER
        bad_ribbon_winner_dec = dict(legit_panties_dec, winner_atom_ids=["atom_neck_ribbon"])
        errs = verify_decision_legality(bad_ribbon_winner_dec, cur_src_map, valid_rules)
        self.assertTrue(any("ILLEGAL_ABSENCE_CONFLICT_WINNER" in e for e in errs), f"got: {errs}")

        # 7. 未知规则 ID -> 拦截 UNKNOWN_DECISION_RULE
        bad_rule_dec = dict(legit_panties_dec, rule_id="invented_unauthorized_rule", reason_code="invented")
        errs = verify_decision_legality(bad_rule_dec, cur_src_map, valid_rules)
        self.assertTrue(any("UNKNOWN_DECISION_RULE" in e for e in errs), f"got: {errs}")

        # 8. before_text 篡改不匹配
        bad_txt_dec = dict(legit_panties_dec, before_text="tampered before text")
        errs = verify_decision_legality(bad_txt_dec, cur_src_map, valid_rules)
        self.assertTrue(any("DECISION_BEFORE_TEXT_MISMATCH" in e for e in errs), f"got: {errs}")

        # 9. 用户复现反例 (Issue 1)：缺少绑定记录被当成没有载体 -> 即使无 cur_bindings，通过生产谓词动态重放仍严格拦截 ILLEGAL_CARRIER_DROP_WHEN_BOUND
        bad_bound_drop_dec = {
            "decision_id": "dec_bound_drop",
            "rule_id": "state_lacks_carrier",
            "reason_code": "state_lacks_carrier",
            "action": "drop",
            "target_atom_id": "atom_wet_state",
            "winner_atom_ids": [],
            "produced_atom_ids": [],
            "before_text": "wet clothes",
            "after_text": "",
        }
        # 即使 cur_bindings 为空，动态重放也会发现 atom_dress 在穿且唯一匹配，拦截非法删除
        errs_no_bindings = verify_decision_legality(bad_bound_drop_dec, cur_src_map, valid_rules, cur_bindings=None)
        self.assertTrue(
            any("ILLEGAL_CARRIER_DROP_WHEN_BOUND" in e for e in errs_no_bindings),
            f"Expected ILLEGAL_CARRIER_DROP_WHEN_BOUND via dynamic predicate replay, got: {errs_no_bindings}"
        )
        # 若传入显式 cur_bindings 同样稳固拦截
        cur_bindings = {
            "atom_wet_state": {
                "carrier_entity_id": "garment:clothing:dress",
                "carrier_selected_id": "dress",
            }
        }
        errs_with_bindings = verify_decision_legality(bad_bound_drop_dec, cur_src_map, valid_rules, cur_bindings=cur_bindings)
        self.assertTrue(
            any("ILLEGAL_CARRIER_DROP_WHEN_BOUND" in e for e in errs_with_bindings),
            f"Expected ILLEGAL_CARRIER_DROP_WHEN_BOUND with cur_bindings, got: {errs_with_bindings}"
        )

        # 10. 用户复现反例 (Issue 2)：普通删除规则绕过语义检查 (以 material_penetration, invented_reason 删除 full body shot)
        bad_generic_drop_dec = {
            "decision_id": "dec_bad_generic",
            "rule_id": "material_penetration",
            "reason_code": "invented_reason",
            "action": "drop",
            "target_atom_id": "atom_framing",
            "winner_atom_ids": [],
            "produced_atom_ids": [],
            "before_text": "full body shot",
            "after_text": "",
        }
        errs_generic = verify_decision_legality(bad_generic_drop_dec, cur_src_map, valid_rules)
        self.assertTrue(
            any("ILLEGAL_REASON_CODE" in e for e in errs_generic),
            f"Expected ILLEGAL_REASON_CODE for invented_reason, got: {errs_generic}"
        )
        self.assertTrue(
            any("ILLEGAL_RULE_TARGET_DOMAIN" in e for e in errs_generic),
            f"Expected ILLEGAL_RULE_TARGET_DOMAIN for framing atom under material_penetration, got: {errs_generic}"
        )

        # 11. 时序漏洞拦截：Winner 原子已被前序决策删除，后续决策不可再次将其充当 Winner
        prior_drop_dec = {
            "decision_id": "dec_prior_drop_underwear",
            "rule_id": "nudity_clothing_conflicts",
            "reason_code": "nudity_removes_clothing",
            "action": "drop",
            "target_atom_id": "atom_basic_underwear",
            "winner_atom_ids": [],
            "produced_atom_ids": [],
            "before_text": "basic underwear",
            "after_text": "",
        }
        subsequent_absence_drop_dec = {
            "decision_id": "dec_subsequent_drop_braless",
            "rule_id": "absence_state_conflict",
            "reason_code": "absence_state_conflict",
            "action": "drop",
            "target_atom_id": "atom_braless",
            "winner_atom_ids": ["atom_basic_underwear"],  # 已在前置决策中被删除！
            "produced_atom_ids": [],
            "before_text": "braless",
            "after_text": "",
        }
        replay_errs = replay_and_verify_decisions(
            list(cur_src_map.values()),
            [prior_drop_dec, subsequent_absence_drop_dec],
            valid_rules,
            enforce_full_completeness=False,
        )
        self.assertTrue(
            any("WINNER_NOT_ACTIVE" in e for e in replay_errs),
            f"Expected WINNER_NOT_ACTIVE for dropped underwear winner, got: {replay_errs}"
        )

        # 12. 用户复现反例 (Causal Grounding Verification)：
        # 输入：在穿 nurse_uniform ＋ full_body_shot (无裸露状态)
        # 伪造决策：以构图原子为 winner，通过 nudity_clothing_conflicts / nudity_removes_clothing 删除 nurse_uniform
        # 生产消解结果：两个原子全部保留，决策为空；审计核验必须拦截 UNGROUNDED_DECISION
        nurse_src_map = {
            "atom_nurse": {
                "atom_id": "atom_nurse",
                "text": "white nurse dress",
                "source_slot": "clothing",
                "source_item_id": "nurse_uniform",
                "garment_topologies": ["top", "bottom_skirt"],
                "is_worn": True,
            },
            "atom_framing": {
                "atom_id": "atom_framing",
                "text": "full body shot",
                "source_slot": "shot_type",
                "source_item_id": "full_body_shot",
                "garment_states": [],
                "garment_topologies": [],
            },
        }
        fake_nudity_drop_dec = {
            "decision_id": "dec_fake_nudity",
            "rule_id": "nudity_clothing_conflicts",
            "reason_code": "nudity_removes_clothing",
            "action": "drop",
            "target_atom_id": "atom_nurse",
            "winner_atom_ids": ["atom_framing"],
            "produced_atom_ids": [],
            "before_text": "white nurse dress",
            "after_text": "",
        }
        grounding_errs = verify_decision_legality(fake_nudity_drop_dec, nurse_src_map, valid_rules)
        self.assertTrue(
            any("UNGROUNDED_DECISION" in e for e in grounding_errs),
            f"Expected UNGROUNDED_DECISION for ungrounded nudity drop of nurse uniform, got: {grounding_errs}",
        )

        # 13. 用户复现反例 5 (Seed 0 产物文本 after_text 篡改拦截)：
        # tattoo_dermal_fusion 实际注入 'pores visible through ink'
        # 若将归档决策的 after_text 改为任意错误文本，完整归档核验与单条决策合法性必须严格拦截
        from nodes import IYKYKPromptGenerator
        gen = IYKYKPromptGenerator()
        inputs = {
            "预设模板": "无 (None)", "风格配方": "无 (None)", "场景大类": "随机 (Random)", "剧情主题": "随机 (Random)",
            "景别构图": "自动 (Auto)", "拍摄视角": "自动 (Auto)", "裸露等级": "随机 (Random)", "服装款式": "随机 (Random)",
            "服装状态": "自动联动裸露等级 (Auto Link Nudity)", "发型发色": "随机 (Random)", "饰品头饰": "随机 (Random)",
            "妆容细节": "随机 (Random)", "姿势动作": "随机 (Random)", "情绪表情": "随机 (Random)", "光影预设": "自动 (Auto)",
            "胶片风格": "随机 (Random)", "液体效果": "随机 (Random)", "纹身标记": "随机 (Random)", "道具物件": "随机 (Random)",
            "角色设定": "随机 (Random)", "真实微瑕": "随机 (Random)", "画质等级": "高清写真 (High)",
        }
        r0 = gen.generate_structured(**inputs, prompt_seed=0)
        seed0_actual_decs = [
            {
                "decision_id": d.decision_id,
                "rule_id": d.rule_id,
                "reason_code": d.reason_code,
                "action": d.action,
                "target_atom_id": d.target_atom_id,
                "winner_atom_ids": list(d.winner_atom_ids),
                "produced_atom_ids": list(d.produced_atom_ids),
                "before_text": d.before_text,
                "after_text": d.after_text,
            }
            for d in r0.resolution_report.decisions
        ]
        # 验证真实决策全量核验通过
        ok_errs = replay_and_verify_decisions(r0.source_atoms, seed0_actual_decs, valid_rules, seed=0)
        self.assertEqual(ok_errs, [])

        # 篡改注入文本 after_text
        tampered_seed0_decs = [dict(d) for d in seed0_actual_decs]
        tampered_seed0_decs[-1]["after_text"] = "TAMPERED_WRONG_TEXT"
        tampered_errs = replay_and_verify_decisions(r0.source_atoms, tampered_seed0_decs, valid_rules, seed=0)
        self.assertTrue(
            any("DECISION_FIELD_MISMATCH(after_text)" in e for e in tampered_errs),
            f"Expected DECISION_FIELD_MISMATCH(after_text) for tampered after_text on seed 0, got: {tampered_errs}"
        )

        # 单条决策接口 verify_decision_legality 同样必须拦截 UNGROUNDED_DECISION
        single_tampered_errs = verify_decision_legality(tampered_seed0_decs[-1], r0.source_atoms, valid_rules, seed=0)
        self.assertTrue(
            any("UNGROUNDED_DECISION" in e for e in single_tampered_errs),
            f"Expected UNGROUNDED_DECISION for single tampered decision, got: {single_tampered_errs}"
        )

        # 14. 用户复现反例 6 (Seed 0 归档决策全部遗漏/部分遗漏拦截)：
        # 输入产生 5 项生产消解，若待审决策列表改成 []，必须拦截 DECISION_COUNT_MISMATCH 与 OMITTED_PRODUCTION_DECISION
        omitted_all_errs = replay_and_verify_decisions(r0.source_atoms, [], valid_rules, seed=0)
        self.assertTrue(
            any("DECISION_COUNT_MISMATCH" in e for e in omitted_all_errs),
            f"Expected DECISION_COUNT_MISMATCH for empty decisions list, got: {omitted_all_errs}"
        )
        self.assertTrue(
            any("OMITTED_PRODUCTION_DECISION" in e for e in omitted_all_errs),
            f"Expected OMITTED_PRODUCTION_DECISION for omitted decisions, got: {omitted_all_errs}"
        )

        # 部分遗漏（少报 1 项）
        omitted_partial_errs = replay_and_verify_decisions(r0.source_atoms, seed0_actual_decs[:-1], valid_rules, seed=0)
        self.assertTrue(
            any("DECISION_COUNT_MISMATCH" in e for e in omitted_partial_errs),
            f"Expected DECISION_COUNT_MISMATCH for partial omission, got: {omitted_partial_errs}"
        )
        self.assertTrue(
            any("OMITTED_PRODUCTION_DECISION" in e for e in omitted_partial_errs),
            f"Expected OMITTED_PRODUCTION_DECISION for partial omission, got: {omitted_partial_errs}"
        )


if __name__ == "__main__":
    unittest.main()

