"""
test_conflict_engine_ssot.py — 冲突消解引擎 17 规则收敛、match_mode、banned_combos 与 17 规则变异测试
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from random import Random

from lib.atomizer import fragments_to_atoms
from lib.conflict_resolver import ConflictResolver, DecisionLedger, OneTimeIndex, match_pattern
from lib.errors import RuleConfigurationError, UnresolvedConflictError
from lib.models import PromptAtom, PromptFragment, SemanticFacts, SpanType, TagProvenance
from lib.rule_contract import (
    RULE_REQUIRED_FIELDS,
    STABLE_RULE_ORDER,
    PatternSpec,
    TextFallbackSpec,
    export_json_schema,
    parse_pattern_spec,
)


class TestConflictEngineSSOT(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(__file__).parent.parent
        self.data_dir = self.repo_dir / "data"
        self.resolver = ConflictResolver(self.data_dir)

    def test_schema_drift_matches_python_contract(self):
        """验证方案 A 单源机制：schemas/conflict-rules.schema.json 与 Python 契约生成物 100% 结构一致，防止漂移"""
        schema_path = self.repo_dir / "schemas" / "conflict-rules.schema.json"
        self.assertTrue(schema_path.exists(), "Schema file does not exist!")

        current_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        generated_schema = export_json_schema()

        self.assertEqual(
            current_schema,
            generated_schema,
            "Repository conflict-rules.schema.json has drifted from lib.rule_contract! "
            "Please run 'python3 scripts/generate_rule_schemas.py' to synchronize."
        )

    def test_match_pattern_modes(self):
        """验证硬约束：exact, word, phrase, regex 匹配模式与严格 Fail-Closed"""
        # 1. exact: 规范化后完整 tag 全等
        self.assertTrue(match_pattern("holding camera", "holding camera", mode="exact"))
        self.assertTrue(match_pattern("holding camera", "  holding camera,  ", mode="exact"))
        self.assertFalse(match_pattern("holding camera", "girl holding camera in room", mode="exact"))

        # 2. word: 仅用于单单词，使用词边界
        self.assertTrue(match_pattern("camera", "camera", mode="word"))
        self.assertTrue(match_pattern("camera", "holding camera", mode="word"))
        self.assertFalse(match_pattern("camera", "cameraman", mode="word"))
        self.assertFalse(match_pattern("pink", "drinking cocktail", mode="word"))

        # 多词短语配置为 word 时必须 Fail-Closed 抛出 RuleConfigurationError，禁止静默升级
        with self.assertRaises(RuleConfigurationError):
            parse_pattern_spec({"pattern": "holding camera", "match_mode": "word"})

        # 3. phrase: 连续短语匹配并限制首尾边界
        self.assertTrue(match_pattern("holding camera", "girl holding camera", mode="phrase"))
        self.assertTrue(match_pattern("holding camera", "holding camera in hand", mode="phrase"))
        self.assertFalse(match_pattern("holding camera", "holding cameraman", mode="phrase"))
        self.assertFalse(match_pattern("holding camera", "still_holding camera", mode="phrase"))

        # 4. regex: 显式正则与非法正则 Fail-Closed
        self.assertTrue(match_pattern(r"^1girl", "1girl, solo", mode="regex"))
        self.assertFalse(match_pattern(r"^1girl", "photo of 1girl", mode="regex"))
        with self.assertRaises(RuleConfigurationError):
            parse_pattern_spec({"pattern": "[unclosed regex", "match_mode": "regex"})

        # 未知 mode 报错
        with self.assertRaises(RuleConfigurationError):
            parse_pattern_spec({"pattern": "pattern", "match_mode": "invalid_mode"})

    def test_typed_specs_negative_mutations(self):
        """
        全套强类型深度负向测试 (复核核心阻断点)：
        1. 字符串代替 dict (如 venue_clusters: "wrong-type")
        2. 整数代替 list (如 outdoor_exclusive: 123)
        3. list item 错类型 (如 outdoor_exclusive: ["string instead of dict"])
        4. nested required 缺失 (如 pattern spec 缺少 match_mode)
        5. 非法 match mode (如 match_mode: "fuzzy")
        6. 非法 regex (如 pattern: "(unclosed")
        7. word 模式包含多词 (如 pattern: "holding camera", match_mode: "word")
        8. 额外未知字段 (如 {"pattern": "x", "match_mode": "phrase", "extra": 1})
        全部必须在加载阶段 Fail-Closed 抛出 RuleConfigurationError！
        """
        raw_rules_doc = json.loads((self.data_dir / "conflict_rules.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp)

            def assert_fails_on_mutation(mutated_doc, err_desc):
                (tmp_data / "conflict_rules.json").write_text(json.dumps(mutated_doc), encoding="utf-8")
                with self.assertRaises(RuleConfigurationError, msg=f"Failed to reject: {err_desc}"):
                    ConflictResolver(tmp_data)

            # 1. 字符串代替 dict
            mut1 = copy.deepcopy(raw_rules_doc)
            rule1 = next(r for r in mut1["rules"] if r["id"] == "spatial_environmental_mutual_exclusion")
            rule1["venue_clusters"] = "wrong-type-string"
            assert_fails_on_mutation(mut1, "string instead of dict for venue_clusters")

            # 2. 整数代替 list
            mut2 = copy.deepcopy(raw_rules_doc)
            rule2 = next(r for r in mut2["rules"] if r["id"] == "spatial_environmental_mutual_exclusion")
            rule2["outdoor_exclusive"] = 12345
            assert_fails_on_mutation(mut2, "int instead of list for outdoor_exclusive")

            # 3. list item 错类型 (纯字符串而非 PatternSpec dict)
            mut3 = copy.deepcopy(raw_rules_doc)
            rule3 = next(r for r in mut3["rules"] if r["id"] == "spatial_environmental_mutual_exclusion")
            rule3["outdoor_exclusive"] = ["plain string instead of dict"]
            assert_fails_on_mutation(mut3, "string in list instead of pattern dict")

            # 4. nested required 缺失 (缺少 match_mode)
            mut4 = copy.deepcopy(raw_rules_doc)
            rule4 = next(r for r in mut4["rules"] if r["id"] == "material_penetration")
            rule4["banned_words"][0] = {"pattern": "sheer"}  # missing match_mode
            assert_fails_on_mutation(mut4, "missing match_mode in pattern spec")

            # 5. 非法 match mode
            mut5 = copy.deepcopy(raw_rules_doc)
            rule5 = next(r for r in mut5["rules"] if r["id"] == "material_penetration")
            rule5["banned_words"][0] = {"pattern": "sheer", "match_mode": "fuzzy_invalid"}
            assert_fails_on_mutation(mut5, "invalid match_mode")

            # 6. 非法 regex
            mut6 = copy.deepcopy(raw_rules_doc)
            rule6 = next(r for r in mut6["rules"] if r["id"] == "material_penetration")
            rule6["banned_words"][0] = {"pattern": "[unclosed", "match_mode": "regex"}
            assert_fails_on_mutation(mut6, "unclosed regex")

            # 7. word 模式包含多词
            mut7 = copy.deepcopy(raw_rules_doc)
            rule7 = next(r for r in mut7["rules"] if r["id"] == "material_penetration")
            rule7["banned_words"][0] = {"pattern": "see through", "match_mode": "word"}
            assert_fails_on_mutation(mut7, "multi-word pattern using word mode")

            # 8. 额外未知字段 (additionalProperties: false)
            mut8 = copy.deepcopy(raw_rules_doc)
            rule8 = next(r for r in mut8["rules"] if r["id"] == "material_penetration")
            rule8["banned_words"][0] = {"pattern": "sheer", "match_mode": "word", "unknown_extra_field": "illegal"}
            assert_fails_on_mutation(mut8, "extra unknown field in pattern spec")

    def test_ssot_fail_closed_on_missing_or_incomplete_rules(self):
        """验证规则配置缺失、少于 17 条或非 JSON 时 Fail-Closed 抛出 RuleConfigurationError"""
        with self.assertRaises(RuleConfigurationError):
            ConflictResolver(Path("/non/existent/path"))

    def test_17_rules_mutation_matrix(self):
        """
        全量 17 规则负向变异测试：
        - 逐条删除任一规则 -> 抛 RuleConfigurationError
        - 复制任一条导致重复 ID -> 抛 RuleConfigurationError
        - 逐条删除任一必填字段或设为空 -> 抛 RuleConfigurationError
        """
        raw_rules_doc = json.loads((self.data_dir / "conflict_rules.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp)

            # 1. 逐条删除测试
            for target_id in STABLE_RULE_ORDER:
                mutated = copy.deepcopy(raw_rules_doc)
                mutated["rules"] = [r for r in mutated["rules"] if r["id"] != target_id]
                (tmp_data / "conflict_rules.json").write_text(json.dumps(mutated), encoding="utf-8")
                with self.assertRaises(RuleConfigurationError, msg=f"Should fail when rule '{target_id}' is missing"):
                    ConflictResolver(tmp_data)

            # 2. 重复 ID 测试 (替换第 2 条为第 1 条的 ID)
            mutated = copy.deepcopy(raw_rules_doc)
            mutated["rules"][1]["id"] = mutated["rules"][0]["id"]
            (tmp_data / "conflict_rules.json").write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaises(RuleConfigurationError, msg="Should fail when duplicate rule ID exists"):
                ConflictResolver(tmp_data)

            # 3. 必填字段缺失测试 (Fail-Closed)
            for rid, req_fields in RULE_REQUIRED_FIELDS.items():
                for f in req_fields:
                    mutated = copy.deepcopy(raw_rules_doc)
                    rule_obj = next(r for r in mutated["rules"] if r["id"] == rid)
                    del rule_obj[f]
                    (tmp_data / "conflict_rules.json").write_text(json.dumps(mutated), encoding="utf-8")
                    with self.assertRaises(RuleConfigurationError, msg=f"Should fail when rule '{rid}' missing required field '{f}'"):
                        ConflictResolver(tmp_data)

            # 4. catalog_* + custom_* 跨字段“合并后非空”约束测试 (R6)
            catalog_pair_rules = [
                ("accessory_occlusion_gaze_coherence", "catalog_occlusion_triggers", "custom_occlusion_triggers"),
                ("accessory_occlusion_gaze_coherence", "catalog_banned_gaze_actions", "custom_banned_gaze_actions"),
                ("framing_lower_body_coherence", "catalog_close_up_triggers", "custom_close_up_triggers"),
                ("framing_lower_body_coherence", "catalog_banned_lower_body", "custom_banned_lower_body"),
                ("pose_hand_occupation", "catalog_busy_pose_triggers", "custom_busy_pose_triggers"),
                ("pose_hand_occupation", "catalog_handheld_patterns", "custom_handheld_patterns"),
                ("environmental_lighting_coherence", "catalog_daylight_triggers", "custom_daylight_triggers"),
                ("environmental_lighting_coherence", "catalog_banned_night_elements", "custom_banned_night_elements"),
                ("monochrome_film_chroma_coherence", "catalog_monochrome_triggers", "custom_monochrome_triggers"),
                ("monochrome_film_chroma_coherence", "catalog_banned_chroma", "custom_banned_chroma"),
                ("makeup_details_coherence", "catalog_no_makeup_triggers", "custom_no_makeup_triggers"),
                ("makeup_details_coherence", "catalog_banned_makeup_smudge", "custom_banned_makeup_smudge"),
            ]
            for rid, cat_key, cust_key in catalog_pair_rules:
                mutated = copy.deepcopy(raw_rules_doc)
                rule_obj = next(r for r in mutated["rules"] if r["id"] == rid)
                rule_obj[cat_key] = []
                rule_obj[cust_key] = []
                (tmp_data / "conflict_rules.json").write_text(json.dumps(mutated), encoding="utf-8")
                with self.assertRaises(RuleConfigurationError, msg=f"Should fail when both {cat_key} and {cust_key} are empty in {rid}"):
                    ConflictResolver(tmp_data)

    def test_rule5_liquid_restrictions_banned_combos(self):
        """
        验证 Rule 5 液体消解：
        - 先执行 banned_combos 替换消解，再应用微量修饰！
        - 断言 cum on closed eyes 替换为 cum on cheek，绝不退化为 'single drop of cum on closed eyes'！
        """
        rng = Random(42)

        test_cases = [
            ("cum on closed eyes", "cum on cheek"),
            ("semen in eyes", "cum on cheek"),
            ("pure white paint-like cum", "translucent slightly viscous fluid"),
            ("milky opaque pussy juice", "clear glistening moisture trail"),
        ]

        for trigger_text, expected_replacement in test_cases:
            frag = PromptFragment(text=trigger_text, source_slot="liquids")
            resolved = self.resolver.resolve_fragments([frag], rng)
            res_texts = [f.text for f in resolved]
            # 必须包含替换后的安全描述
            self.assertTrue(
                any(expected_replacement in t for t in res_texts),
                f"Expected '{expected_replacement}' in {res_texts}"
            )
            # 必须不包含原崩图组合
            self.assertFalse(
                any(trigger_text in t for t in res_texts),
                f"Banned trigger '{trigger_text}' still found in {res_texts}"
            )

    def test_rule3_clothing_extension_provenance_preservation(self):
        """验证 Rule 3 材质穿透消解中：官方 Provenance 扩展标签受控豁免，用户 sheer 标签被替换"""
        rng = Random(42)

        # 1. 官方扩展标签（带 clothing_extension provenance），在 L3 下 100% 保留
        official_ext_frag = PromptFragment(
            text="semi-translucent fabric",
            source_slot="clothing",
            provenance=TagProvenance(
                item_id="tier_trans_2",
                semantic_ids=("extension_family:cloth_transparency", "nudity:L3"),
                kind="clothing_extension"
            )
        )
        res = self.resolver.resolve_fragments([official_ext_frag], rng)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].text, "semi-translucent fabric")

        # 2. 无官方 provenance 的普通 tag，包含 see-through / sheer，在非 L1 下被替换
        user_sheer_frag = PromptFragment(
            text="see-through blouse",
            source_slot="clothing",
        )
        res = self.resolver.resolve_fragments([user_sheer_frag], rng)
        res_texts = [f.text for f in res]
        self.assertFalse(any("see-through" in t for t in res_texts))

    def test_rule13_framing_lower_body_tag_level_precision(self):
        """验证 Rule 13 在 close-up 下仅精准剔除下肢标签 (high heels)，同槽其他标签 (silk robe) 完好保留"""
        rng = Random(42)
        frags = [
            PromptFragment(text="extreme close-up", source_slot="shot_type", order=0),
            PromptFragment(text="silk robe", source_slot="clothing", order=1),
            PromptFragment(text="high heels", source_slot="clothing", order=2),
            PromptFragment(text="earrings", source_slot="jewelry", order=3),
        ]
        resolved = self.resolver.resolve_fragments(frags, rng)
        resolved_texts = [f.text for f in resolved]
        self.assertIn("extreme close-up", resolved_texts)
        self.assertIn("silk robe", resolved_texts)
        self.assertIn("earrings", resolved_texts)
        self.assertNotIn("high heels", resolved_texts)

    def test_rule14_accessory_occlusion_tag_level_precision(self):
        """验证 Rule 14 在蒙眼布下仅剔除视线对视标签，保留饰品与其他面部标签"""
        rng = Random(42)
        frags = [
            PromptFragment(text="blindfold", source_slot="jewelry", order=0),
            PromptFragment(text="looking at viewer", source_slot="expression", order=1),
            PromptFragment(text="pearl necklace", source_slot="jewelry", order=2),
        ]
        resolved = self.resolver.resolve_fragments(frags, rng)
        resolved_texts = [f.text for f in resolved]
        self.assertIn("blindfold", resolved_texts)
        self.assertIn("pearl necklace", resolved_texts)
        self.assertNotIn("looking at viewer", resolved_texts)

    def test_rule15_monochrome_chroma_tag_level_precision(self):
        """验证 Rule 15 在黑白胶片下仅剔除高饱和色彩标签，保留光影与氛围标签"""
        rng = Random(42)
        frags = [
            PromptFragment(text="classic monochrome", source_slot="film", order=0),
            PromptFragment(text="neon rim lighting", source_slot="lighting", order=1),
            PromptFragment(text="dramatic chiaroscuro", source_slot="lighting", order=2),
        ]
        resolved = self.resolver.resolve_fragments(frags, rng)
        resolved_texts = [f.text for f in resolved]
        self.assertIn("classic monochrome", resolved_texts)
        self.assertIn("dramatic chiaroscuro", resolved_texts)
        self.assertNotIn("neon rim lighting", resolved_texts)

    def test_rule16_clothing_style_state_tag_level_precision(self):
        """验证 Rule 16 在连体泳衣下仅剔除掀裙/解纽扣标签，保留泳衣本身"""
        rng = Random(42)
        frags = [
            PromptFragment(text="school swimsuit (sukumizu)", source_slot="clothing", order=0),
            PromptFragment(text="unbuttoned blouse", source_slot="clothing", order=1),
            PromptFragment(text="wet skin", source_slot="liquids", order=2),
        ]
        resolved = self.resolver.resolve_fragments(frags, rng)
        resolved_texts = [f.text for f in resolved]
        self.assertIn("school swimsuit (sukumizu)", resolved_texts)
        self.assertIn("wet skin", resolved_texts)
        self.assertNotIn("unbuttoned blouse", resolved_texts)

    def test_protected_blackbox_spans_never_modified_or_dropped(self):
        """验证 ANGLE (<lora:...>) 与 QUOTED ("...") 绝不被任何规则修改或剔除"""
        rng = Random(42)
        atoms = [
            PromptAtom(text="<lora:high_heels:1.0>", span_type=SpanType.ANGLE, source_slot="clothing", tag_order=0),
            PromptAtom(text='"extreme close-up high heels"', span_type=SpanType.QUOTED, source_slot="clothing", tag_order=1),
            PromptAtom(text="extreme close-up", span_type=SpanType.PLAIN, source_slot="shot_type", tag_order=2),
        ]
        resolved = self.resolver.resolve_atoms(atoms, rng)
        self.assertEqual(len(resolved), 3)
        self.assertEqual(resolved[0].text, "<lora:high_heels:1.0>")
        self.assertEqual(resolved[1].text, '"extreme close-up high heels"')

    def test_nested_blackbox_in_brackets_never_deleted_across_all_17_rules(self):
        """
        验证强制反例 P1-2：括号内嵌 LoRA 或 Quoted 时，即使与其它元素冲突，
        根据 Fail-Safe 规则必须整块保留该结构，绝不破坏或删除黑盒。
        """
        rng = Random(42)
        # 1. 场景互斥冲突下测试
        frags = [
            PromptFragment(text="beach at night", source_slot="scene_theme"),
            PromptFragment(text="(classroom <lora:x:1>:1.2)", source_slot="scene_theme"),
            PromptFragment(text="[classroom \"exact phrase\":1.2]", source_slot="scene_theme"),
        ]
        tags, raw_atoms = fragments_to_atoms(frags)
        for a in raw_atoms:
            if "<lora:x:1>" in a.text or "\"exact phrase\"" in a.text:
                self.assertTrue(a.contains_blackbox)
                self.assertFalse(a.can_delete_atom)

        with self.assertRaises(UnresolvedConflictError) as cm:
            self.resolver.resolve_atoms_with_report(raw_atoms, rng)
        self.assertEqual(cm.exception.reason, "protected_syntax_conflict")

        # 2. 校验在所有 17 条规则的结构中，含黑盒后代的原子 can_delete_atom 恒为 False
        bracket_atom_with_lora = PromptAtom(
            text="(classroom <lora:test:1.0>:1.1)",
            span_type=SpanType.PAREN,
            source_slot="scene_theme",
            contains_blackbox=True,
        )
        self.assertFalse(bracket_atom_with_lora.can_delete_atom)

    def test_level_rules_l1_to_l6_schema_and_contract_mandatory(self):
        """
        验证强制反例 P1-3：裸露等级 L1～L6 逐级必须存在且非空。
        逐项删除 L1～L6，断言 validate_rule_document 与 Draft-7 均直接拦截报错。
        """
        import copy
        import json
        import jsonschema
        from lib.rule_contract import export_json_schema, validate_rule_document
        from lib.errors import RuleConfigurationError

        schema = export_json_schema()
        validator = jsonschema.Draft7Validator(schema)

        orig_data = json.loads(Path(self.data_dir, "conflict_rules.json").read_text(encoding="utf-8"))
        nudity_idx = next(idx for idx, r in enumerate(orig_data["rules"]) if r["id"] == "nudity_clothing_conflicts")

        for i in range(1, 7):
            lvl_key = f"L{i}"
            mutated = copy.deepcopy(orig_data)
            del mutated["rules"][nudity_idx]["level_rules"][lvl_key]

            # 1. 权威 Python 契约单源校验必须抛错
            with self.assertRaises(RuleConfigurationError, msg=f"validate_rule_document did not fail when {lvl_key} deleted"):
                validate_rule_document(mutated)

            # 2. Draft-7 Schema 校验必须检测出 required 属性缺失
            errors = list(validator.iter_errors(mutated))
            self.assertTrue(len(errors) > 0, f"Draft-7 validator did not catch missing {lvl_key}")

    def test_exact_catalog_index_collision_on_different_objects_same_id(self):
        """
        验证强制反例 P2-1：同一 Catalog 内两个不同 dict 对象使用相同 ID/key 时无条件 Fail-Closed。
        """
        from lib.sampler import ExactCatalogIndex
        from lib.errors import CatalogIndexingError

        index = ExactCatalogIndex("test_catalog")
        item1 = {"id": "same_id", "name_zh": "测试项1"}
        item2 = {"id": "same_id", "name_zh": "测试项2"}

        index.register_item(item1)
        # 再次注册同一对象不同字段为幂等合法
        index.register_item(item1)

        # 注册不同对象且具有冲突 key 时必须立即抛出 CatalogIndexingError
        with self.assertRaises(CatalogIndexingError):
            index.register_item(item2)

    def test_schema_and_runtime_parser_differential_contract(self):
        """
        验证必须修订 7：Draft-7 Schema 与 Python 运行时 parse_rule_document 差分契约。
        断言合法的 conflict_rules.json 均被两者接受；
        对多种结构变异（缺字段、类型错误、未识别规则、多余规则），两者均无遗漏拦截！
        """
        import jsonschema
        from lib.rule_contract import export_json_schema, parse_rule_document, RuleDocument

        schema = export_json_schema()
        validator = jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker())
        orig_data = json.loads(Path(self.data_dir, "conflict_rules.json").read_text(encoding="utf-8"))

        # 1. 原始文件两者均 100% 验收通过
        self.assertEqual(list(validator.iter_errors(orig_data)), [])
        doc = parse_rule_document(orig_data)
        self.assertIsInstance(doc, RuleDocument)
        self.assertEqual(len(doc.rules), 17)

        def get_rule_dict(d, rule_id):
            return next(r for r in d["rules"] if r.get("id") == rule_id)

        def mutate_remove_req(d):
            get_rule_dict(d, "material_penetration").pop("banned_words")
            return d

        def mutate_invalid_match_mode(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0]["match_mode"] = "fuzzy"
            return d

        def mutate_missing_pattern(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0].pop("pattern")
            return d

        def mutate_duplicate_id(d):
            # 将规则 0 复制覆盖规则 1，保持 17 项但重复一个 ID 且缺少一个 ID (P1-3)
            d["rules"][1] = copy.deepcopy(d["rules"][0])
            return d

        def mutate_word_multiword_space(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "sheer fabric", "match_mode": "word"}
            return d

        def mutate_word_multiword_tab(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "sheer\tfabric", "match_mode": "word"}
            return d

        def mutate_word_hyphen(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "see-through", "match_mode": "word"}
            return d

        def mutate_word_slash(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "top/bottom", "match_mode": "word"}
            return d

        def mutate_invalid_regex(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "[", "match_mode": "regex"}
            return d

        def mutate_valid_regex(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": r"^valid_.*$", "match_mode": "regex"}
            return d

        def mutate_blank_pattern_empty(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "", "match_mode": "phrase"}
            return d

        def mutate_blank_pattern_spaces(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "   ", "match_mode": "phrase"}
            return d

        def mutate_blank_pattern_tab_newline(d):
            get_rule_dict(d, "material_penetration")["banned_words"][0] = {"pattern": "\t\n", "match_mode": "phrase"}
            return d

        def mutate_empty_cluster_key(d):
            r = get_rule_dict(d, "spatial_environmental_mutual_exclusion")
            first_val = list(r["venue_clusters"].values())[0]
            r["venue_clusters"][""] = first_val
            return d

        def mutate_whitespace_spaces_cluster_key(d):
            r = get_rule_dict(d, "spatial_environmental_mutual_exclusion")
            first_val = list(r["venue_clusters"].values())[0]
            r["venue_clusters"]["   "] = first_val
            return d

        def mutate_whitespace_tab_newline_cluster_key(d):
            r = get_rule_dict(d, "spatial_environmental_mutual_exclusion")
            first_val = list(r["venue_clusters"].values())[0]
            r["venue_clusters"]["\t\n"] = first_val
            return d

        def mutate_valid_cluster_key_ascii(d):
            r = get_rule_dict(d, "spatial_environmental_mutual_exclusion")
            first_val = list(r["venue_clusters"].values())[0]
            r["venue_clusters"]["cyberpunk_alley"] = first_val
            return d

        def mutate_valid_cluster_key_unicode(d):
            r = get_rule_dict(d, "spatial_environmental_mutual_exclusion")
            first_val = list(r["venue_clusters"].values())[0]
            r["venue_clusters"]["赛博朋克后巷"] = first_val
            return d

        def set_rule_field(d, rule_id, field, value):
            for r in d["rules"]:
                if r.get("id") == rule_id:
                    r[field] = value
                    return d
            raise ValueError(f"Rule {rule_id} not found")

        mutations = [
            # 缺失 rules 顶层
            ("empty", lambda d: {}),
            # 规则数量不足
            ("insufficient_rules", lambda d: {"rules": d["rules"][:10]}),
            # 缺失必需字段
            ("remove_req", mutate_remove_req),
            # match_mode 非法取值
            ("invalid_match_mode", mutate_invalid_match_mode),
            # 模式缺失 pattern 键
            ("missing_pattern", mutate_missing_pattern),
            # P1-3 新增反例：重复规则 ID (17项中重复1个缺1个)
            ("duplicate_id", mutate_duplicate_id),
            # P1-3 新增反例：word 模式包含空格
            ("word_multiword_space", mutate_word_multiword_space),
            # P1-3 新增反例：word 模式包含 Tab
            ("word_multiword_tab", mutate_word_multiword_tab),
            # P1-3 新增反例：word 模式包含连字符
            ("word_hyphen", mutate_word_hyphen),
            # P1-3 新增反例：word 模式包含斜杠
            ("word_slash", mutate_word_slash),
            # P1-3 新增反例：非法正则表达式
            ("invalid_regex", mutate_invalid_regex),
            # P1-3 正向等价样例：合法正则表达式
            ("valid_regex", mutate_valid_regex),
            # P1-3 新增反例：纯空白模式 (空字符串)
            ("blank_pattern_empty", mutate_blank_pattern_empty),
            # P1-3 新增反例：纯空白模式 (空格)
            ("blank_pattern_spaces", mutate_blank_pattern_spaces),
            # P1-3 新增反例：纯空白模式 (Tab/换行)
            ("blank_pattern_tab_newline", mutate_blank_pattern_tab_newline),
            # P1-1 新增反例：venue_clusters 空键与纯空白键拦截
            ("empty_cluster_key", mutate_empty_cluster_key),
            ("whitespace_spaces_cluster_key", mutate_whitespace_spaces_cluster_key),
            ("whitespace_tab_newline_cluster_key", mutate_whitespace_tab_newline_cluster_key),
            # P1-1 正向样例：合法 ASCII 与 Unicode 键
            ("valid_cluster_key_ascii", mutate_valid_cluster_key_ascii),
            ("valid_cluster_key_unicode", mutate_valid_cluster_key_unicode),
            # 全规则关键字段类型错误变异矩阵
            ("spatial_venue_clusters_str", lambda d: set_rule_field(d, "spatial_environmental_mutual_exclusion", "venue_clusters", "wrong-type")),
            ("spatial_outdoor_str", lambda d: set_rule_field(d, "spatial_environmental_mutual_exclusion", "outdoor_exclusive", "wrong-type")),
            ("nudity_conflicts_str", lambda d: set_rule_field(d, "nudity_clothing_conflicts", "conflicts", "wrong-type")),
            ("nudity_level_rules_str", lambda d: set_rule_field(d, "nudity_clothing_conflicts", "level_rules", "wrong-type")),
            ("material_replacements_str", lambda d: set_rule_field(d, "material_penetration", "replacements", "wrong-type")),
            ("clothing_coherence_one_piece_str", lambda d: set_rule_field(d, "clothing_style_state_coherence", "one_piece_triggers", "wrong-type")),
            ("gaze_geometry_mappings_str", lambda d: set_rule_field(d, "gaze_angle_geometry", "mappings", "wrong-type")),
            ("gaze_mutex_pairs_str", lambda d: set_rule_field(d, "gaze_mutual_exclusion", "exclusive_pairs", "wrong-type")),
            ("accessory_triggers_str", lambda d: set_rule_field(d, "accessory_occlusion_gaze_coherence", "catalog_occlusion_triggers", "wrong-type")),
            ("framing_triggers_str", lambda d: set_rule_field(d, "framing_lower_body_coherence", "catalog_close_up_triggers", "wrong-type")),
            ("liquid_combos_str", lambda d: set_rule_field(d, "liquid_restrictions", "banned_combos", "wrong-type")),
            ("device_constraints_str", lambda d: set_rule_field(d, "device_quality_compatibility", "device_constraints", "wrong-type")),
            ("tattoo_fusion_tags_str", lambda d: set_rule_field(d, "tattoo_dermal_fusion", "fusion_tags", "wrong-type")),
            ("pose_busy_triggers_str", lambda d: set_rule_field(d, "pose_hand_occupation", "catalog_busy_pose_triggers", "wrong-type")),
            ("props_patterns_str", lambda d: set_rule_field(d, "handheld_props_single_holder", "handheld_patterns", "wrong-type")),
            ("emotion_conflicts_str", lambda d: set_rule_field(d, "emotion_gaze_affinity", "conflicts", "wrong-type")),
            ("lighting_daylight_str", lambda d: set_rule_field(d, "environmental_lighting_coherence", "catalog_daylight_triggers", "wrong-type")),
            ("monochrome_triggers_str", lambda d: set_rule_field(d, "monochrome_film_chroma_coherence", "catalog_monochrome_triggers", "wrong-type")),
            ("makeup_triggers_str", lambda d: set_rule_field(d, "makeup_details_coherence", "catalog_no_makeup_triggers", "wrong-type")),
        ]

        for name, fn in mutations:
            mut_data = fn(copy.deepcopy(orig_data))

            schema_errors = list(validator.iter_errors(mut_data))
            schema_accepts = (len(schema_errors) == 0)

            try:
                parse_rule_document(mut_data)
                parser_accepts = True
            except Exception:
                parser_accepts = False

            # P1-1/P1-3 核心断言：覆盖约定变异语料的一致性 (Consistency across representative mutation corpus)
            self.assertEqual(
                schema_accepts,
                parser_accepts,
                f"Consistency mismatch for {name}: schema_accepts={schema_accepts} (errors: {[e.message for e in schema_errors]}), parser_accepts={parser_accepts}"
            )

            positive_samples = {"valid_regex", "valid_cluster_key_ascii", "valid_cluster_key_unicode"}
            if name not in positive_samples:
                self.assertFalse(schema_accepts, f"Schema unexpectedly accepted invalid mutation: {name}")
                self.assertFalse(parser_accepts, f"Parser unexpectedly accepted invalid mutation: {name}")
            else:
                self.assertTrue(schema_accepts, f"Schema rejected valid sample: {name}")
                self.assertTrue(parser_accepts, f"Parser rejected valid sample: {name}")

    def test_r6_metadata_vs_runtime_fields_and_runtime_mutation_behavior(self):
        """
        验证 R6:
        1. 声明式契约中严格区分 metadata_fields 与 runtime_fields
        2. 元数据字段 (description, name_zh) 的修改不影响规则消解执行行为
        3. 运行字段的修改导致对应规则的输出或触发报告发生预期行为变化 (行为反例)
        """
        from lib.rule_contract import RULE_DESCRIPTORS

        # 1. 契约层检查：所有 17 规则必须明确划分元数据与运行字段
        for rid, desc in RULE_DESCRIPTORS.items():
            meta = [f.name for f in desc.fields if not f.is_runtime]
            runtime = [f.name for f in desc.fields if f.is_runtime]
            self.assertIn("description", meta, f"Rule {rid} missing description in metadata")
            self.assertGreater(len(runtime), 0, f"Rule {rid} must have at least one runtime field")
            if any(f.name == "name_zh" for f in desc.fields):
                self.assertIn("name_zh", meta, f"Rule {rid} name_zh must be marked metadata (is_runtime=False)")

        # 2. 元数据修改无行为副作用反例：修改 description 不改变消解结果
        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp) / "data"
            tmp_data.mkdir()
            for f in self.data_dir.glob("*.json"):
                (tmp_data / f.name).write_bytes(f.read_bytes())

            raw_rules = json.loads((tmp_data / "conflict_rules.json").read_text(encoding="utf-8"))
            for r in raw_rules["rules"]:
                r["description"] = f"Mutated description for {r['id']}"
            (tmp_data / "conflict_rules.json").write_text(json.dumps(raw_rules), encoding="utf-8")

            res_mut = ConflictResolver(tmp_data)
            test_slots = {
                "scene_theme": ["classroom", "forest"],
                "pose": ["two hands on hips"],
                "props": ["holding sword"],
            }
            orig_out = self.resolver.resolve(test_slots, rng=Random(42))
            mut_out = res_mut.resolve(test_slots, rng=Random(42))
            self.assertEqual(orig_out, mut_out, "Changing metadata descriptions should not affect resolution output!")

        # 3. 运行字段修改必然引起行为变化反例 (Behavior Counter-Examples)
        # 行为反例 A: Rule 12 pose_hand_occupation
        # 原逻辑：compact camera in hand + hands behind back -> 命中冲突，compact camera in hand 被移除
        frags = [
            PromptFragment(text="hands behind back", source_slot="pose"),
            PromptFragment(text="compact camera in hand", source_slot="props"),
        ]
        resolved_orig, rules_orig = self.resolver.resolve_atoms_with_report(
            fragments_to_atoms(frags)[1], rng=Random(42)
        )
        self.assertIn("pose_hand_occupation", rules_orig)
        self.assertNotIn("compact camera in hand", [a.text for a in resolved_orig])

        # 变异运行字段：将 custom_busy_pose_triggers 设为 unrelated，清空 catalog_busy_pose_triggers
        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp) / "data"
            tmp_data.mkdir()
            for f in self.data_dir.glob("*.json"):
                (tmp_data / f.name).write_bytes(f.read_bytes())

            raw_rules = json.loads((tmp_data / "conflict_rules.json").read_text(encoding="utf-8"))
            for r in raw_rules["rules"]:
                if r["id"] == "pose_hand_occupation":
                    r["catalog_busy_pose_triggers"] = []
                    r["custom_busy_pose_triggers"] = [{"pattern": "unrelated_trigger", "match_mode": "phrase"}]
                    r["text_fallback"]["patterns"] = [
                        {"pattern": "unrelated_trigger", "match_mode": "phrase", "role": "trigger", "group_id": "pose_hand"},
                        {"pattern": "unrelated_handheld", "match_mode": "phrase", "role": "handheld", "group_id": "pose_hand"},
                    ]
            (tmp_data / "conflict_rules.json").write_text(json.dumps(raw_rules), encoding="utf-8")

            res_mut = ConflictResolver(tmp_data)
            resolved_mut, rules_mut = res_mut.resolve_atoms_with_report(
                fragments_to_atoms(frags)[1], rng=Random(42)
            )
            # 行为改变断言：规则不再触发，compact camera in hand 完好保留！
            self.assertNotIn("pose_hand_occupation", rules_mut)
            self.assertIn("compact camera in hand", [a.text for a in resolved_mut])

        # 行为反例 B: Rule 7 accessory_occlusion_gaze_coherence
        # 原逻辑：blindfold + making eye contact -> 视线动作被移除
        banned_gaze = "making eye contact with camera then breaking away shyly"
        frags_b = [
            PromptFragment(text="blindfold", source_slot="jewelry"),
            PromptFragment(text=banned_gaze, source_slot="expression"),
        ]
        resolved_orig_b, rules_orig_b = self.resolver.resolve_atoms_with_report(
            fragments_to_atoms(frags_b)[1], rng=Random(42)
        )
        self.assertIn("accessory_occlusion_gaze_coherence", rules_orig_b)
        self.assertNotIn(banned_gaze, [a.text for a in resolved_orig_b])

        # 变异运行字段：清空 catalog_occlusion_triggers，保留 unrelated custom
        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp) / "data"
            tmp_data.mkdir()
            for f in self.data_dir.glob("*.json"):
                (tmp_data / f.name).write_bytes(f.read_bytes())

            raw_rules = json.loads((tmp_data / "conflict_rules.json").read_text(encoding="utf-8"))
            for r in raw_rules["rules"]:
                if r["id"] == "accessory_occlusion_gaze_coherence":
                    r["catalog_occlusion_triggers"] = []
                    r["custom_occlusion_triggers"] = [{"pattern": "unrelated_occlusion", "match_mode": "phrase"}]
                    r["text_fallback"]["patterns"] = [
                        {"pattern": "unrelated_occlusion", "match_mode": "phrase", "role": "trigger", "group_id": "occlusion"},
                        {"pattern": "unrelated_banned", "match_mode": "phrase", "role": "banned", "group_id": "occlusion"},
                    ]
            (tmp_data / "conflict_rules.json").write_text(json.dumps(raw_rules), encoding="utf-8")

            res_mut_b = ConflictResolver(tmp_data)
            resolved_mut_b, rules_mut_b = res_mut_b.resolve_atoms_with_report(
                fragments_to_atoms(frags_b)[1], rng=Random(42)
            )
            # 行为改变断言：规则不再触发，视线动作完好保留！
            self.assertNotIn("accessory_occlusion_gaze_coherence", rules_mut_b)
            self.assertIn(banned_gaze, [a.text for a in resolved_mut_b])


    def test_rule_contract_descriptor_self_validation(self):
        """测试描述符定义期自校验：重复字段、'id' 在 fields、dataclass 字段缺失/多余等拦截 (P2-3)"""
        from dataclasses import dataclass
        from typing import Any, Tuple
        from lib.rule_contract import (
            RuleContractDescriptor,
            StringField,
            PatternArrayField,
        )

        @dataclass(frozen=True)
        class DummySpec:
            id: str
            field_a: str
            field_b: Tuple[Any, ...]

        # 1. 字段名重复报错
        with self.assertRaises(ValueError) as ctx:
            RuleContractDescriptor(
                "dummy",
                DummySpec,
                [
                    StringField("field_a"),
                    StringField("field_a"),
                ],
            )
        self.assertIn("Duplicate field names", str(ctx.exception))

        # 2. 'id' 出现在 fields 中报错
        with self.assertRaises(ValueError) as ctx:
            RuleContractDescriptor(
                "dummy",
                DummySpec,
                [
                    StringField("id"),
                    StringField("field_a"),
                ],
            )
        self.assertIn("'id' must not appear", str(ctx.exception))

        # 3. 目标类非 dataclass 报错
        class NotADataclass:
            pass

        with self.assertRaises(TypeError) as ctx:
            RuleContractDescriptor(
                "dummy",
                NotADataclass,
                [StringField("field_a")],
            )
        self.assertIn("must be a dataclass class", str(ctx.exception))

        # 3b. 目标对象为 dataclass 实例而非类对象报错 (P2)
        dummy_instance = DummySpec("dummy_id", "val_a", ("val_b",))
        with self.assertRaises(TypeError) as ctx:
            RuleContractDescriptor(
                "dummy",
                dummy_instance,
                [
                    StringField("field_a"),
                    PatternArrayField("field_b"),
                ],
            )
        self.assertIn("must be a dataclass class", str(ctx.exception))

        # 4. dataclass 字段缺失定义报错
        with self.assertRaises(ValueError) as ctx:
            RuleContractDescriptor(
                "dummy",
                DummySpec,
                [StringField("field_a")],  # 缺少 field_b
            )
        self.assertIn("Field definition mismatch", str(ctx.exception))
        self.assertIn("missing={'field_b'}", str(ctx.exception))

        # 5. descriptor 多出未在 dataclass 声明的字段报错
        with self.assertRaises(ValueError) as ctx:
            RuleContractDescriptor(
                "dummy",
                DummySpec,
                [
                    StringField("field_a"),
                    PatternArrayField("field_b"),
                    StringField("field_c"),  # 多出 field_c
                ],
            )
        self.assertIn("Field definition mismatch", str(ctx.exception))
        self.assertIn("extra={'field_c'}", str(ctx.exception))

        # 6. dataclass 缺少 'id' 字段报错 (P2-3)
        @dataclass(frozen=True)
        class MissingIdSpec:
            field_a: str

        with self.assertRaises(ValueError) as ctx:
            RuleContractDescriptor(
                "dummy",
                MissingIdSpec,
                [StringField("field_a")],
            )
        self.assertIn("must define an 'id' field", str(ctx.exception))

        # 7. dataclass 的 'id' 字段 init=False 报错 (P2-3)
        from dataclasses import field

        @dataclass(frozen=True)
        class InitFalseIdSpec:
            id: str = field(init=False, default="dummy")
            field_a: str = "val"

        with self.assertRaises(ValueError) as ctx:
            RuleContractDescriptor(
                "dummy",
                InitFalseIdSpec,
                [StringField("field_a")],
            )
        self.assertIn("must have init=True", str(ctx.exception))

    def test_material_penetration_real_catalog_scope_protection(self):
        """测试 Rule 3 声明式作用域：真实词库中的非服装碰撞词 100% 保持原样，服装词精准替换 (P1-2, P2-2)"""
        cases = [
            ("makeup", "sheer lip balm", False),
            ("makeup", "sheer nude lip gloss", False),
            ("style_recipe", "sheer curtain", False),
            ("lighting", "soft sunlight filtered through sheer curtains", False),
            ("jewelry", "sheer patterned eye mask", False),
            ("scene_theme", "see-through glass window", False),
            ("clothing", "sheer blouse", True),  # 服装槽位正常触发
        ]

        for slot, text, should_replace in cases:
            frags = [PromptFragment(text=text, source_slot=slot, order=0)]
            _, atoms = fragments_to_atoms(frags)
            resolved = self.resolver.resolve_atoms(atoms, Random(42))
            res_text = resolved[0].text
            if should_replace:
                self.assertNotEqual(res_text, text, f"{text!r} in {slot} should be replaced")
            else:
                self.assertEqual(res_text, text, f"{text!r} in {slot} must NOT be replaced")

        # 官方 clothing_extension 优先豁免
        from lib.models import TagProvenance
        ext_frags = [
            PromptFragment(
                text="sheer lingerie",
                source_slot="clothing",
                order=0,
                provenance=TagProvenance(kind="clothing_extension"),
            )
        ]
        _, ext_atoms = fragments_to_atoms(ext_frags)
        resolved_ext = self.resolver.resolve_atoms(ext_atoms, Random(42))
        self.assertEqual(resolved_ext[0].text, "sheer lingerie")

        # 全量扫描 makeup, lighting, accessories, style_recipes 词库，断言零碰撞词被误改 (P2-2)
        from lib.assembler import render_atoms

        catalog_slot_map = {
            "makeup.json": "makeup",
            "lighting.json": "lighting",
            "accessories.json": "jewelry",
            "style_recipes.json": "style_recipe",
        }

        def extract_leaf_strings(obj):
            if isinstance(obj, str):
                yield obj
            elif isinstance(obj, list):
                for x in obj:
                    yield from extract_leaf_strings(x)
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield from extract_leaf_strings(v)

        for cat_file, slot_name in catalog_slot_map.items():
            cat_path = self.data_dir / cat_file
            self.assertTrue(cat_path.is_file(), f"Missing catalog file: {cat_file}")
            cat_data = json.loads(cat_path.read_text(encoding="utf-8"))

            all_leaf_strings = list(extract_leaf_strings(cat_data))
            # 强制非空断言：扫描到的叶子字符串总数 > 0
            self.assertGreater(
                len(all_leaf_strings),
                0,
                f"File {cat_file} yielded 0 leaf strings! Scanner failed to recurse into containers."
            )

            matched_collision_tags = [
                txt for txt in all_leaf_strings
                if any(w in txt.lower() for w in ("sheer", "see-through", "transparent"))
            ]
            # 强制非空断言：当前词库至少命中 1 个目标碰撞词样本
            self.assertGreater(
                len(matched_collision_tags),
                0,
                f"File {cat_file} yielded 0 collision samples! Target keywords not matched."
            )

            for txt in matched_collision_tags:
                f = [PromptFragment(text=txt, source_slot=slot_name, order=0)]
                _, test_atoms = fragments_to_atoms(f)
                orig_rendered = render_atoms(test_atoms)
                resolved = self.resolver.resolve_atoms(test_atoms, Random(42))
                res_rendered = render_atoms(resolved)
                self.assertEqual(
                    res_rendered,
                    orig_rendered,
                    f"Non-clothing catalog tag in {cat_file} (slot: {slot_name}) was improperly mutated: {orig_rendered!r} -> {res_rendered!r}"
                )

        # 变异自检：如果同样包含 sheer 的词位于服装槽位 (clothing)，则必须发生替换
        f_clothing = [PromptFragment(text="sheer blouse", source_slot="clothing", order=0)]
        _, clothing_atoms = fragments_to_atoms(f_clothing)
        c_orig = render_atoms(clothing_atoms)
        c_res = render_atoms(self.resolver.resolve_atoms(clothing_atoms, Random(42)))
        self.assertNotEqual(c_orig, c_res, "Clothing slot sheer blouse should have been mutated by Rule 3")

    def test_ssot_frozen_fields_disk_mutations_and_runtime_injection(self):
        """R2R2-P1-001 深度门禁：
        1. 磁盘变异：删除/缩减 17 规则中任意一条的 target_slots 必须被解析器拒绝 (RuleConfigurationError)。
        2. 磁盘变异：删除/缩减 17 规则中任意一条的 fact_fields 必须被解析器拒绝 (RuleConfigurationError)。
        3. 磁盘变异：删除/缩减/篡改 17 规则中任意一条的 text_fallback.patterns 必须被解析器拒绝 (RuleConfigurationError)。
        4. 运行时：未知正式 ID + 空/不足 facts 必须 Fail-Closed 拒绝 (RuleConfigurationError)。
        5. 运行时：正式 Atom 自带 facts 与 catalog 权威 facts 冲突必须 Fail-Closed 拒绝 (RuleConfigurationError)。
        6. 运行时：测试 custom fallback 只能消费 RuleItem 中声明的 text_fallback.patterns，通过注入自定义 RuleItem 验证处理器真实生效。
        """
        raw_rules_doc = json.loads((self.data_dir / "conflict_rules.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp)

            def assert_fails_on_doc(mutated_doc, err_desc):
                (tmp_data / "conflict_rules.json").write_text(json.dumps(mutated_doc), encoding="utf-8")
                with self.assertRaises(RuleConfigurationError, msg=f"Failed to reject: {err_desc}"):
                    ConflictResolver(tmp_data)

            # 1. 变异 target_slots (缩减/清空)
            mutated_1 = copy.deepcopy(raw_rules_doc)
            for r in mutated_1["rules"]:
                if r["id"] == "nudity_clothing_conflicts":
                    r["semantic_constraints"]["target_slots"] = ["nudity"]  # 缩减
                    break
            assert_fails_on_doc(mutated_1, "reduced target_slots in nudity_clothing_conflicts")

            # 2. 变异 fact_fields (清空)
            mutated_2 = copy.deepcopy(raw_rules_doc)
            for r in mutated_2["rules"]:
                if r["id"] == "spatial_environmental_mutual_exclusion":
                    r["semantic_constraints"]["fact_fields"] = []
                    break
            assert_fails_on_doc(mutated_2, "empty fact_fields in spatial_environmental_mutual_exclusion")

            # 3. 变异 text_fallback.patterns (篡改 pattern)
            mutated_3 = copy.deepcopy(raw_rules_doc)
            for r in mutated_3["rules"]:
                if r["id"] == "pose_hand_occupation":
                    r["text_fallback"]["patterns"] = [{"pattern": "fake_pattern_xyz", "match_mode": "phrase"}]
                    break
            assert_fails_on_doc(mutated_3, "altered text_fallback.patterns in pose_hand_occupation")

        # 4. 运行时：未知正式 ID + 空 facts 必须 Fail-Closed 拒绝
        from lib.models import PromptAtom, SelectionOrigin, SpanType, TagProvenance, SemanticFacts
        unknown_atom = PromptAtom(
            atom_id="atom_unknown_001",
            text="unknown cyber attire",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="completely_unknown_formal_id_xyz",
            provenance=TagProvenance(item_id="completely_unknown_formal_id_xyz"),
            origin=SelectionOrigin(mode="preset", selector="clothing", selected_id="completely_unknown_formal_id_xyz"),
            facts=SemanticFacts(),
        )
        with self.assertRaises(RuleConfigurationError) as ctx_unknown:
            self.resolver.resolve_atoms_with_full_report([unknown_atom], Random(42))
        self.assertIn("unrecognized item ID", str(ctx_unknown.exception))

        # 5. 运行时：正式 Atom facts 与 catalog 权威 facts 冲突必须 Fail-Closed 拒绝
        conflict_atom = PromptAtom(
            atom_id="atom_suite_001",
            text="honeymoon suite",
            span_type=SpanType.PLAIN,
            source_slot="scene",
            source_item_id="scene_honeymoon_suite__tag_000",
            provenance=TagProvenance(item_id="scene_honeymoon_suite__tag_000"),
            origin=SelectionOrigin(mode="preset", selector="scene", selected_id="scene_honeymoon_suite__tag_000"),
            facts=SemanticFacts(space_kind="outdoor", time_of_day="day"),  # 故意与 catalog 中的 indoor 冲突
        )
        with self.assertRaises(RuleConfigurationError) as ctx_conflict:
            self.resolver.resolve_atoms_with_full_report([conflict_atom], Random(42))
        self.assertIn("conflicts with catalog", str(ctx_conflict.exception))

        # 6. 运行时：注入自定义 RuleItem，验证 custom fallback 真实消费 RuleItem 中编译的 text_fallback.patterns
        from lib.rule_contract import HandheldPropsRuleSpec, RuleItem, TextFallbackSpec
        injected_pattern_a = PatternSpec(pattern="custom_exclusive_gizmo_a", match_mode="phrase")
        injected_pattern_b = PatternSpec(pattern="custom_exclusive_gizmo_b", match_mode="phrase")
        injected_spec = HandheldPropsRuleSpec(
            id="handheld_props_single_holder",
            description="test injection",
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            handheld_patterns=(injected_pattern_a, injected_pattern_b),
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=TextFallbackSpec(
                strategy="custom",
                enabled=True,
                target_slots=("props",),
                patterns=(injected_pattern_a, injected_pattern_b),
            ),
        )
        injected_rule_item = RuleItem(
            id="handheld_props_single_holder",
            description="test injection",
            spec=injected_spec,
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=injected_spec.text_fallback,
        )

        prop_a = PromptAtom(
            atom_id="atom_prop_a",
            text="custom_exclusive_gizmo_a",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )
        prop_b = PromptAtom(
            atom_id="atom_prop_b",
            text="custom_exclusive_gizmo_b",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )

        from lib.conflict_resolver import OneTimeIndex, DecisionLedger
        test_index = OneTimeIndex([prop_a, prop_b])
        test_ledger = DecisionLedger(self.resolver.registry)
        res_atoms = self.resolver._resolve_handheld_props_single_holder_atoms(
            [prop_a, prop_b],
            injected_rule_item,
            test_ledger,
            Random(42),
            test_index,
            None,
        )
        self.assertEqual(len(res_atoms), 1, "Injected RuleItem fallback patterns should have resolved conflict to 1 atom")
        self.assertEqual(len(test_ledger.decisions), 1, "Injected RuleItem fallback patterns should have produced 1 decision")

    def test_r2r3_p1_001_fallback_exclusively_consumes_rule_item_text_fallback(self):
        """反例验证 R2R3-P1-001: fallback 必须严格且仅消费 RuleItem.spec.text_fallback.patterns。
        1. 正例反例：RuleItem 的处理器专属集合为空 (handheld_patterns=())，但 text_fallback.patterns 声明了两个有效模式 ->
           必须成功命中 fallback 并消解冲突 (1 drop, 1 decision)。
        2. 负例反例：RuleItem 的处理器专属集合包含两个有效模式，但 text_fallback.patterns 为空 (patterns=()) ->
           必须 Fail-Closed，绝不命中 fallback (0 drop, 0 decisions, 2 atoms 原样保留)。
        """
        from lib.conflict_resolver import DecisionLedger, OneTimeIndex
        from lib.models import PromptAtom, SelectionOrigin, SemanticFacts, SpanType
        from lib.rule_contract import HandheldPropsRuleSpec, PatternSpec, RuleItem, TextFallbackSpec

        p_a = PatternSpec(pattern="exclusive_gizmo_alpha", match_mode="phrase")
        p_b = PatternSpec(pattern="exclusive_gizmo_beta", match_mode="phrase")

        atom_a = PromptAtom(
            atom_id="atom_gizmo_a",
            text="exclusive_gizmo_alpha",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )
        atom_b = PromptAtom(
            atom_id="atom_gizmo_b",
            text="exclusive_gizmo_beta",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )

        # 1. 专属集合为空，仅 text_fallback.patterns 有效 -> 必须命中
        rule_spec_pos = HandheldPropsRuleSpec(
            id="handheld_props_single_holder",
            description="positive fallback test",
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            handheld_patterns=(),  # 专属集合为空！
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=TextFallbackSpec(
                strategy="custom",
                enabled=True,
                target_slots=("props",),
                patterns=(p_a, p_b),  # 唯一事实源！
            ),
        )
        rule_item_pos = RuleItem(
            id="handheld_props_single_holder",
            description="positive fallback test",
            spec=rule_spec_pos,
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=rule_spec_pos.text_fallback,
        )

        idx_pos = OneTimeIndex([atom_a, atom_b])
        ledger_pos = DecisionLedger(self.resolver.registry)
        res_pos = self.resolver._resolve_handheld_props_single_holder_atoms(
            [atom_a, atom_b],
            rule_item_pos,
            ledger_pos,
            Random(42),
            idx_pos,
            None,
        )
        self.assertEqual(len(res_pos), 1, "Empty handler patterns + valid text_fallback.patterns must resolve conflict to 1 atom")
        self.assertEqual(len(ledger_pos.decisions), 1, "Must produce 1 drop decision")

        # 2. 专属集合有词，但 text_fallback.patterns 为空 -> 绝不命中 (Fail-Closed)
        rule_spec_neg = HandheldPropsRuleSpec(
            id="handheld_props_single_holder",
            description="negative fallback test",
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            handheld_patterns=(p_a, p_b),  # 专属集合有词
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=TextFallbackSpec(
                strategy="custom",
                enabled=True,
                target_slots=("props",),
                patterns=(),  # fallback 为空！
            ),
        )
        rule_item_neg = RuleItem(
            id="handheld_props_single_holder",
            description="negative fallback test",
            spec=rule_spec_neg,
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=rule_spec_neg.text_fallback,
        )

        idx_neg = OneTimeIndex([atom_a, atom_b])
        ledger_neg = DecisionLedger(self.resolver.registry)
        res_neg = self.resolver._resolve_handheld_props_single_holder_atoms(
            [atom_a, atom_b],
            rule_item_neg,
            ledger_neg,
            Random(42),
            idx_neg,
            None,
        )
        self.assertEqual(len(res_neg), 2, "Valid handler patterns + empty text_fallback.patterns must NOT resolve conflict")
        self.assertEqual(len(ledger_neg.decisions), 0, "Must produce 0 decisions")

    def test_r2r3_p3_001_fact_and_mutex_index_integration_and_tombstone(self):
        """反例验证 R2R3-P3-001: 事实、ID 与互斥组索引直接参与候选原子发现，且墓碑直接导致候选差分。"""
        from lib.conflict_resolver import DecisionLedger, OneTimeIndex
        from lib.models import PromptAtom, SelectionOrigin, SemanticFacts, SpanType

        atom_space = PromptAtom(
            atom_id="atom_space_01",
            text="indoor living room",
            span_type=SpanType.PLAIN,
            source_slot="scene",
            origin=SelectionOrigin(mode="preset", selector="scene"),
            facts=SemanticFacts(space_kind="indoor"),
        )
        atom_prop = PromptAtom(
            atom_id="atom_prop_01",
            text="prop coffee mug",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="preset", selector="props"),
            facts=SemanticFacts(prop_usage="handheld", hands_required=1),
        )
        atom_busy = PromptAtom(
            atom_id="atom_pose_01",
            text="hands tied behind back",
            span_type=SpanType.PLAIN,
            source_slot="pose",
            origin=SelectionOrigin(mode="preset", selector="pose"),
            facts=SemanticFacts(hand_state="both_busy", mutex_groups=("busy_hands",)),
        )

        index = OneTimeIndex([atom_space, atom_prop, atom_busy])

        # 1. 索引查询方法实际返回匹配原子
        self.assertEqual([a.atom_id for a in index.get_active_by_fact("space_kind", "indoor")], ["atom_space_01"])
        self.assertEqual([a.atom_id for a in index.get_active_by_fact("prop_usage", "handheld")], ["atom_prop_01"])
        self.assertEqual([a.atom_id for a in index.get_active_by_mutex_group("busy_hands")], ["atom_pose_01"])

        # 2. 墓碑差分测试：当 atom_prop_01 被墓碑标记后，相关事实索引立即排除该原子
        index.tombstone("atom_prop_01")
        self.assertFalse(index.is_active("atom_prop_01"))
        self.assertEqual([a.atom_id for a in index.get_active_by_fact("prop_usage", "handheld")], [])

        # 3. 墓碑直接改变规则候选与消解结果：
        #    在单持道具规则中，如果候选已被前序规则墓碑，则不参与单持消解
        rule_item = self.resolver.registry.get_rule_item("handheld_props_single_holder")
        ledger = DecisionLedger(self.resolver.registry)
        remaining = self.resolver._resolve_handheld_props_single_holder_atoms(
            [atom_prop],
            rule_item,
            ledger,
            Random(42),
            index,
            None,
        )
        self.assertEqual(len(ledger.decisions), 0, "Tombstoned prop must not generate further drop decisions")

    def test_r2r4_p1_001_non_intersecting_handler_does_not_mask_declared_pattern(self):
        """反例验证 R2R4-P1-001 (1): 非空但不相交的旧 handler 集合不得屏蔽 text_fallback 声明 pattern。"""
        from lib.conflict_resolver import DecisionLedger, OneTimeIndex
        from lib.models import PromptAtom, SelectionOrigin, SemanticFacts, SpanType
        from lib.rule_contract import HandheldPropsRuleSpec, PatternSpec, RuleItem, TextFallbackSpec

        p_legacy = PatternSpec(pattern="unrelated_legacy_gadget", match_mode="phrase", role="handheld")
        p_decl_a = PatternSpec(pattern="declared_gadget_a", match_mode="phrase", role="handheld")
        p_decl_b = PatternSpec(pattern="declared_gadget_b", match_mode="phrase", role="handheld")

        rule_spec = HandheldPropsRuleSpec(
            id="handheld_props_single_holder",
            description="non-intersecting handler test",
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            handheld_patterns=(p_legacy,),  # 非空旧 handler 集合，与声明完全不相交！
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=TextFallbackSpec(
                strategy="custom",
                enabled=True,
                target_slots=("props",),
                patterns=(p_decl_a, p_decl_b),  # 声明性 fallback pattern
            ),
        )
        rule_item = RuleItem(
            id="handheld_props_single_holder",
            description="non-intersecting handler test",
            spec=rule_spec,
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=rule_spec.text_fallback,
        )

        atom_a = PromptAtom(
            atom_id="atom_gadget_a",
            text="declared_gadget_a",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )
        atom_b = PromptAtom(
            atom_id="atom_gadget_b",
            text="declared_gadget_b",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )

        idx = OneTimeIndex([atom_a, atom_b])
        ledger = DecisionLedger(self.resolver.registry)
        res = self.resolver._resolve_handheld_props_single_holder_atoms(
            [atom_a, atom_b],
            rule_item,
            ledger,
            Random(42),
            idx,
            None,
        )
        self.assertEqual(len(res), 1, "Non-intersecting handler patterns MUST NOT mask declared text_fallback patterns")
        self.assertEqual(len(ledger.decisions), 1, "Must resolve conflict and produce 1 decision")
        self.assertEqual(ledger.decisions[0].target_atom_id, "atom_gadget_b")

    def test_r2r4_p1_001_empty_declaration_prevents_old_handler_patterns_from_triggering(self):
        """反例验证 R2R4-P1-001 (2): text_fallback 声明为空时，旧 handler pattern 绝不得触发 (Fail-Closed)。"""
        from lib.conflict_resolver import DecisionLedger, OneTimeIndex
        from lib.models import PromptAtom, SelectionOrigin, SemanticFacts, SpanType
        from lib.rule_contract import HandheldPropsRuleSpec, PatternSpec, RuleItem, TextFallbackSpec

        p_legacy_a = PatternSpec(pattern="legacy_gadget_a", match_mode="phrase", role="handheld")
        p_legacy_b = PatternSpec(pattern="legacy_gadget_b", match_mode="phrase", role="handheld")

        rule_spec = HandheldPropsRuleSpec(
            id="handheld_props_single_holder",
            description="empty declaration test",
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            handheld_patterns=(p_legacy_a, p_legacy_b),  # 旧 handler 集合非空
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=TextFallbackSpec(
                strategy="custom",
                enabled=True,
                target_slots=("props",),
                patterns=(),  # 声明为空！
            ),
        )
        rule_item = RuleItem(
            id="handheld_props_single_holder",
            description="empty declaration test",
            spec=rule_spec,
            priority=210,
            phase="physical",
            depends_on=("pose_hand_occupation",),
            reason_codes=("single_handheld_prop_limit",),
            text_fallback=rule_spec.text_fallback,
        )

        atom_a = PromptAtom(
            atom_id="atom_legacy_a",
            text="legacy_gadget_a",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )
        atom_b = PromptAtom(
            atom_id="atom_legacy_b",
            text="legacy_gadget_b",
            span_type=SpanType.PLAIN,
            source_slot="props",
            origin=SelectionOrigin(mode="custom", selector="props"),
            facts=SemanticFacts(),
        )

        idx = OneTimeIndex([atom_a, atom_b])
        ledger = DecisionLedger(self.resolver.registry)
        res = self.resolver._resolve_handheld_props_single_holder_atoms(
            [atom_a, atom_b],
            rule_item,
            ledger,
            Random(42),
            idx,
            None,
        )
        self.assertEqual(len(res), 2, "Empty text_fallback declaration MUST NOT trigger conflict resolution from legacy patterns")
        self.assertEqual(len(ledger.decisions), 0, "Must produce 0 decisions when text_fallback declaration is empty")

    def test_r2r4_p3_001_get_active_by_item_id_affects_candidates_and_decisions(self):
        """行为差分验证 R2R4-P3-001: get_active_by_item_id() 真实影响规则候选与胜负。
        1. 正例：非标准 slot 中的 close_up 原子通过 get_active_by_item_id 成功被规则捕获为胜者，触发下身衣物剔除。
        2. 差分：若该 close_up 原子被 tombstone，get_active_by_item_id 返回空，下身衣物完好保留 (0 decision)。
        """
        from lib.conflict_resolver import DecisionLedger, OneTimeIndex
        from lib.models import PromptAtom, SelectionOrigin, SemanticFacts, SpanType, TagProvenance

        cu_atom = PromptAtom(
            atom_id="atom_cu_01",
            text="extreme close-up face shot",
            span_type=SpanType.PLAIN,
            source_slot="custom_camera_framing",  # 非标准 shot_type 槽位！
            source_item_id="extreme_close_up",
            provenance=TagProvenance(item_id="extreme_close_up"),
            origin=SelectionOrigin(mode="preset", selector="shot", selected_id="extreme_close_up"),
            facts=SemanticFacts(visible_regions=("face",)),
        )
        skirt_atom = PromptAtom(
            atom_id="atom_skirt_01",
            text="pleated skirt",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            origin=SelectionOrigin(mode="preset", selector="clothing"),
            facts=SemanticFacts(visible_regions=("lower_body", "feet"), garment_topologies=("bottom_skirt",)),
        )

        # 1. 活跃状态：get_active_by_item_id 捕获候选并胜出，剔除 skirt
        idx_active = OneTimeIndex([cu_atom, skirt_atom])
        cu_query = idx_active.get_active_by_item_id("extreme_close_up")
        self.assertEqual([a.atom_id for a in cu_query], ["atom_cu_01"])

        rule_item = self.resolver.registry.get_rule_item("framing_lower_body_coherence")
        ledger_active = DecisionLedger(self.resolver.registry)
        res_active = self.resolver._resolve_framing_lower_body_coherence_atoms(
            [cu_atom, skirt_atom],
            rule_item,
            ledger_active,
            Random(42),
            idx_active,
            None,
        )
        self.assertEqual(len(ledger_active.decisions), 1, "Active item_id candidate must trigger lower-body drop")
        self.assertEqual(ledger_active.decisions[0].target_atom_id, "atom_skirt_01")
        self.assertEqual(ledger_active.decisions[0].winner_atom_ids, ("atom_cu_01",))

        # 2. 墓碑状态：get_active_by_item_id 排除墓碑，候选为空，skirt 不被剔除
        idx_tomb = OneTimeIndex([cu_atom, skirt_atom])
        idx_tomb.tombstone("atom_cu_01")
        cu_tomb_query = idx_tomb.get_active_by_item_id("extreme_close_up")
        self.assertEqual(cu_tomb_query, [], "Tombstoned item_id must return empty active list")

        ledger_tomb = DecisionLedger(self.resolver.registry)
        res_tomb = self.resolver._resolve_framing_lower_body_coherence_atoms(
            [cu_atom, skirt_atom],
            rule_item,
            ledger_tomb,
            Random(42),
            idx_tomb,
            None,
        )
        self.assertEqual(len(ledger_tomb.decisions), 0, "When item_id candidate is tombstoned, rule must not drop clothing")

    def test_all_17_rules_sentinel_behavior_matrix(self):
        """反例验证 R2R5-P1-001: 17 条规则全量 sentinel 行为矩阵验证。
        - 正例断言：只修改声明 pattern、保持旧集合不变时必须触发 (produce >0 decisions)；
        - 负例断言：声明清空时旧集合不得触发 (Fail-Closed, produce 0 decisions)。
        """
        sentinel_fixtures = {
            "spatial_environmental_mutual_exclusion": {
                "pos_patterns": (
                    PatternSpec("sentinel_indoor_v1", "phrase", role="indoor", group_id="indoor"),
                    PatternSpec("sentinel_outdoor_v1", "phrase", role="outdoor", group_id="outdoor"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_indoor_v1", span_type=SpanType.PLAIN, source_slot="scene", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_outdoor_v1", span_type=SpanType.PLAIN, source_slot="scene", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="indoor onsen", span_type=SpanType.PLAIN, source_slot="scene", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="rotenburo", span_type=SpanType.PLAIN, source_slot="scene", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "nudity_clothing_conflicts": {
                "pos_patterns": (
                    PatternSpec("sentinel_trig_nude", "phrase", role="trigger", group_id="conflict_sentinel"),
                    PatternSpec("sentinel_ban_cloth", "phrase", role="banned", group_id="conflict_sentinel"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_trig_nude", span_type=SpanType.PLAIN, source_slot="nudity", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_ban_cloth", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="completely naked", span_type=SpanType.PLAIN, source_slot="nudity", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="wearing uniform", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "framing_lower_body_coherence": {
                "pos_patterns": (
                    PatternSpec("sentinel_cu_shot", "phrase", role="trigger", group_id="framing"),
                    PatternSpec("sentinel_lb_boots", "phrase", role="banned", group_id="framing"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_cu_shot", span_type=SpanType.PLAIN, source_slot="shot_type", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_lb_boots", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="close-up", span_type=SpanType.PLAIN, source_slot="shot_type", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="boots", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "pose_hand_occupation": {
                "pos_patterns": (
                    PatternSpec("sentinel_busy_pose", "phrase", role="trigger", group_id="pose_hand"),
                    PatternSpec("sentinel_handheld_gizmo", "phrase", role="handheld", group_id="pose_hand"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_busy_pose", span_type=SpanType.PLAIN, source_slot="pose", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_handheld_gizmo", span_type=SpanType.PLAIN, source_slot="props", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="hands behind back", span_type=SpanType.PLAIN, source_slot="pose", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="holding smartphone", span_type=SpanType.PLAIN, source_slot="props", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "handheld_props_single_holder": {
                "pos_patterns": (
                    PatternSpec("sentinel_prop_a", "phrase", role="handheld", group_id="handheld"),
                    PatternSpec("sentinel_prop_b", "phrase", role="handheld", group_id="handheld"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_prop_a", span_type=SpanType.PLAIN, source_slot="props", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_prop_b", span_type=SpanType.PLAIN, source_slot="props", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="holding smartphone", span_type=SpanType.PLAIN, source_slot="props", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="holding camera", span_type=SpanType.PLAIN, source_slot="props", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "clothing_style_state_coherence": {
                "pos_patterns": (
                    PatternSpec("sentinel_onepiece_suit", "phrase", role="trigger", group_id="one_piece"),
                    PatternSpec("sentinel_unbuttoned_blouse", "phrase", role="banned", group_id="one_piece"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_onepiece_suit", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_unbuttoned_blouse", span_type=SpanType.PLAIN, source_slot="clothing_state", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="one-piece swimsuit", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="unbuttoned blouse", span_type=SpanType.PLAIN, source_slot="clothing_state", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "material_penetration": {
                "pos_patterns": (
                    PatternSpec("sentinel_sheer_fabric", "phrase", role="banned", group_id="material"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_sheer_fabric", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="see-through", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                ],
            },
            "device_quality_compatibility": {
                "pos_patterns": (
                    PatternSpec("sentinel_device_cam", "phrase", role="device", group_id="sentinel_dev"),
                    PatternSpec("sentinel_banned_quality", "phrase", role="banned", group_id="sentinel_dev"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_device_cam", span_type=SpanType.PLAIN, source_slot="shot_type", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_banned_quality", span_type=SpanType.PLAIN, source_slot="quality", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="cctv", span_type=SpanType.PLAIN, source_slot="shot_type", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="masterpiece", span_type=SpanType.PLAIN, source_slot="quality", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "environmental_lighting_coherence": {
                "pos_patterns": (
                    PatternSpec("sentinel_daylight_glow", "phrase", role="trigger", group_id="daylight"),
                    PatternSpec("sentinel_midnight_shadow", "phrase", role="banned", group_id="daylight"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_daylight_glow", span_type=SpanType.PLAIN, source_slot="lighting", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_midnight_shadow", span_type=SpanType.PLAIN, source_slot="lighting", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="natural sunlight", span_type=SpanType.PLAIN, source_slot="lighting", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="night shadows", span_type=SpanType.PLAIN, source_slot="lighting", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "monochrome_film_chroma_coherence": {
                "pos_patterns": (
                    PatternSpec("sentinel_bw_film", "phrase", role="trigger", group_id="monochrome"),
                    PatternSpec("sentinel_neon_color", "phrase", role="banned", group_id="monochrome"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_bw_film", span_type=SpanType.PLAIN, source_slot="film", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_neon_color", span_type=SpanType.PLAIN, source_slot="lighting", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="black and white", span_type=SpanType.PLAIN, source_slot="film", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="vibrant neon colors", span_type=SpanType.PLAIN, source_slot="lighting", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "makeup_details_coherence": {
                "pos_patterns": (
                    PatternSpec("sentinel_bare_skin", "phrase", role="trigger", group_id="no_makeup"),
                    PatternSpec("sentinel_heavy_eyeliner", "phrase", role="banned", group_id="no_makeup"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_bare_skin", span_type=SpanType.PLAIN, source_slot="makeup", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_heavy_eyeliner", span_type=SpanType.PLAIN, source_slot="makeup", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="no makeup", span_type=SpanType.PLAIN, source_slot="makeup", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="smudged eyeliner", span_type=SpanType.PLAIN, source_slot="makeup", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "gaze_angle_geometry": {
                "pos_patterns": (
                    PatternSpec("sentinel_high_angle", "phrase", role="angle", group_id="high_angle"),
                    PatternSpec("sentinel_banned_look", "phrase", role="banned", group_id="high_angle"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_high_angle", span_type=SpanType.PLAIN, source_slot="camera_angle", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_banned_look", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="high angle", span_type=SpanType.PLAIN, source_slot="camera_angle", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="looking up", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "accessory_occlusion_gaze_coherence": {
                "pos_patterns": (
                    PatternSpec("sentinel_eye_mask", "phrase", role="trigger", group_id="occlusion"),
                    PatternSpec("sentinel_direct_stare", "phrase", role="banned", group_id="occlusion"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_eye_mask", span_type=SpanType.PLAIN, source_slot="jewelry", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_direct_stare", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="blindfold", span_type=SpanType.PLAIN, source_slot="jewelry", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="looking at viewer", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "emotion_gaze_affinity": {
                "pos_patterns": (
                    PatternSpec("sentinel_shy_emotion", "phrase", role="emotion", group_id="shy"),
                    PatternSpec("sentinel_seductive_gaze", "phrase", role="banned", group_id="shy"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_shy_emotion", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_seductive_gaze", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="shy", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="seductive smile", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "gaze_mutual_exclusion": {
                "pos_patterns": (
                    PatternSpec("sentinel_stare_straight", "phrase", role="exclusive_a", group_id="pair_sentinel"),
                    PatternSpec("sentinel_look_sideways", "phrase", role="exclusive_b", group_id="pair_sentinel"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_stare_straight", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="sentinel_look_sideways", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="looking at viewer", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                    PromptAtom(atom_id="a1", text="looking away", span_type=SpanType.PLAIN, source_slot="expression", origin=None, facts=SemanticFacts(), tag_order=1, span_order=1),
                ],
            },
            "liquid_restrictions": {
                "pos_patterns": (
                    PatternSpec("sentinel_cum_eyes", "phrase", role="trigger", group_id="cum_eyes"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_cum_eyes", span_type=SpanType.PLAIN, source_slot="liquids", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="cum on closed eyes", span_type=SpanType.PLAIN, source_slot="liquids", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                ],
            },
            "tattoo_dermal_fusion": {
                "pos_patterns": (
                    PatternSpec("sentinel_dragon_ink", "phrase", role="tattoo", group_id="tattoo"),
                ),
                "pos_atoms": [
                    PromptAtom(atom_id="a0", text="sentinel_dragon_ink", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                ],
                "neg_atoms": [
                    PromptAtom(atom_id="a0", text="dragon tattoo", span_type=SpanType.PLAIN, source_slot="clothing", origin=None, facts=SemanticFacts(), tag_order=0, span_order=0),
                ],
            },
        }

        for rid in self.resolver.registry.doc.execution_order:
            self.assertIn(rid, sentinel_fixtures, f"Sentinel fixture missing for rule {rid}")
            fixture = sentinel_fixtures[rid]
            rule_item = self.resolver.registry.get_rule_item(rid)
            handler = getattr(self.resolver, f"_resolve_{rid}_atoms")

            # 1. Positive: text_fallback 仅有 sentinel pattern，旧 handler 集合保持不变 -> 必须触发
            pos_tf = TextFallbackSpec(
                strategy="pattern_match",
                enabled=True,
                target_slots=rule_item.spec.text_fallback.target_slots,
                patterns=fixture["pos_patterns"],
            )
            pos_item = replace(rule_item, text_fallback=pos_tf, spec=replace(rule_item.spec, text_fallback=pos_tf))
            pos_idx = OneTimeIndex(fixture["pos_atoms"])
            pos_ledger = DecisionLedger(self.resolver.registry)
            handler(fixture["pos_atoms"], pos_item, pos_ledger, Random(42), pos_idx, None)
            self.assertGreater(
                len(pos_ledger.decisions),
                0,
                f"[{rid}] Positive sentinel failed: declared fallback patterns must trigger rule without touching old collections",
            )

            # 2. Negative: text_fallback 清空，旧 handler 集合保持不变 -> 旧词绝不触发 (Fail-Closed)
            neg_tf = TextFallbackSpec(
                strategy="pattern_match",
                enabled=True,
                target_slots=rule_item.spec.text_fallback.target_slots,
                patterns=(),
            )
            neg_item = replace(rule_item, text_fallback=neg_tf, spec=replace(rule_item.spec, text_fallback=neg_tf))
            neg_idx = OneTimeIndex(fixture["neg_atoms"])
            neg_ledger = DecisionLedger(self.resolver.registry)
            handler(fixture["neg_atoms"], neg_item, neg_ledger, Random(42), neg_idx, None)
            self.assertEqual(
                len(neg_ledger.decisions),
                0,
                f"[{rid}] Negative sentinel failed: empty fallback declaration must NOT trigger old handler collections",
            )



    def test_group_id_fail_closed_contract_validation(self):
        """验证 group_id 契约 Fail-Closed：拒绝未知 group_id、拼写错误、单侧重组、孤立分组与缺失成对 role。"""
        from lib.rule_contract import FROZEN_RULE_GROUPS, parse_rule_document

        raw_rules = json.loads((self.data_dir / "conflict_rules.json").read_text(encoding="utf-8"))

        # 1. 负向变异 1: 未知 group_id / 拼写错误 (indor vs indoor) -> 必须被拦截
        bad_rules_1 = copy.deepcopy(raw_rules)
        for r in bad_rules_1["rules"]:
            if r["id"] == "spatial_environmental_mutual_exclusion":
                r["text_fallback"]["patterns"][0]["group_id"] = "indor"
        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(bad_rules_1)
        self.assertIn("Unknown or invalid group_id 'indor'", str(ctx.exception))

        # 2. 负向变异 2: 非法 role/group 组合 (在 indoor 组声明 outdoor role) -> 必须被拦截
        bad_rules_2 = copy.deepcopy(raw_rules)
        for r in bad_rules_2["rules"]:
            if r["id"] == "spatial_environmental_mutual_exclusion":
                r["text_fallback"]["patterns"][0]["role"] = "outdoor"
        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(bad_rules_2)
        self.assertIn("Invalid role 'outdoor' for group 'indoor'", str(ctx.exception))

        # 3. 负向变异 3: 缺少必需分组 / 孤立分组 (剔除 pants 分组) -> 必须被拦截
        bad_rules_3 = copy.deepcopy(raw_rules)
        for r in bad_rules_3["rules"]:
            if r["id"] == "clothing_style_state_coherence":
                r["text_fallback"]["patterns"] = [
                    p for p in r["text_fallback"]["patterns"] if p["group_id"] != "pants"
                ]
        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(bad_rules_3)
        self.assertIn("missing required groups", str(ctx.exception))

        # 4. 负向变异 4: 缺失成对 role (conflict_0 组仅有 trigger，丢失 banned) -> 必须被拦截
        bad_rules_4 = copy.deepcopy(raw_rules)
        for r in bad_rules_4["rules"]:
            if r["id"] == "nudity_clothing_conflicts":
                r["text_fallback"]["patterns"] = [
                    p for p in r["text_fallback"]["patterns"]
                    if not (p["group_id"] == "conflict_0" and p["role"] == "banned")
                ]
        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(bad_rules_4)
        self.assertIn("missing required paired roles", str(ctx.exception))

        # 5. 负向变异 5: 固定单组规则使用错误组名 (material_penetration 组名不是 material) -> 必须被拦截
        bad_rules_5 = copy.deepcopy(raw_rules)
        for r in bad_rules_5["rules"]:
            if r["id"] == "material_penetration":
                for p in r["text_fallback"]["patterns"]:
                    p["group_id"] = "general_material"
        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(bad_rules_5)
        self.assertIn("Unknown or invalid group_id 'general_material'", str(ctx.exception))

        # 6. 负向变异 6: 基数违规 (L6 组基数超过上限或为 0) -> 必须被拦截
        bad_rules_6 = copy.deepcopy(raw_rules)
        for r in bad_rules_6["rules"]:
            if r["id"] == "nudity_clothing_conflicts":
                r["text_fallback"]["patterns"] = [
                    p for p in r["text_fallback"]["patterns"] if p["group_id"] != "L6"
                ]
        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(bad_rules_6)
        self.assertIn("missing required groups", str(ctx.exception))

        # 7. 负向变异 7: 完全重复 pattern 声明 (同规则内声明重复的 pattern, match_mode, role, group_id) -> 必须被拦截 (P2)
        bad_rules_7 = copy.deepcopy(raw_rules)
        for r in bad_rules_7["rules"]:
            if r["id"] == "spatial_environmental_mutual_exclusion":
                r["text_fallback"]["patterns"].append(copy.deepcopy(r["text_fallback"]["patterns"][0]))
        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(bad_rules_7)
        self.assertIn("Duplicate fallback pattern declaration", str(ctx.exception))

        # 8. 负向变异 8: 尝试篡改深度不可变冻结 group 契约 (P2)
        with self.assertRaises(TypeError):
            FROZEN_RULE_GROUPS["spatial_environmental_mutual_exclusion"] = {}

        with self.assertRaises(TypeError):
            FROZEN_RULE_GROUPS["spatial_environmental_mutual_exclusion"]["allowed_groups"] = ()

        with self.assertRaises(TypeError):
            FROZEN_RULE_GROUPS["spatial_environmental_mutual_exclusion"]["group_required_roles"]["indoor"] = ()

        with self.assertRaises(TypeError):
            FROZEN_RULE_GROUPS["spatial_environmental_mutual_exclusion"]["role_cardinality"][("indoor", "indoor")] = (0, 0)

        with self.assertRaises(TypeError):
            del FROZEN_RULE_GROUPS["spatial_environmental_mutual_exclusion"]

    def test_501_patterns_bijective_partition_and_reachability(self):
        """证明全部 501 个 fallback pattern 均被且仅被一个生产 selector 消费，且每个 selector 均消费 >= 1 个 pattern。"""
        raw_rules = json.loads((self.data_dir / "conflict_rules.json").read_text(encoding="utf-8"))
        rule_map = {r["id"]: r for r in raw_rules["rules"]}

        production_selectors = [
            # 1. spatial
            ("spatial_environmental_mutual_exclusion", "indoor", "indoor"),
            ("spatial_environmental_mutual_exclusion", "outdoor", "outdoor"),
            ("spatial_environmental_mutual_exclusion", "school", "venue"),
            ("spatial_environmental_mutual_exclusion", "bedroom", "venue"),
            ("spatial_environmental_mutual_exclusion", "office", "venue"),
            ("spatial_environmental_mutual_exclusion", "dining", "venue"),
            ("spatial_environmental_mutual_exclusion", "onsen", "venue"),
            ("spatial_environmental_mutual_exclusion", "transport", "venue"),
            # 2. nudity
            ("nudity_clothing_conflicts", "L1", "banned"),
            ("nudity_clothing_conflicts", "L2", "banned"),
            ("nudity_clothing_conflicts", "L3", "banned"),
            ("nudity_clothing_conflicts", "L4", "banned"),
            ("nudity_clothing_conflicts", "L5", "banned"),
            ("nudity_clothing_conflicts", "L6", "banned"),
            ("nudity_clothing_conflicts", "conflict_0", "trigger"),
            ("nudity_clothing_conflicts", "conflict_0", "banned"),
            ("nudity_clothing_conflicts", "conflict_1", "trigger"),
            ("nudity_clothing_conflicts", "conflict_1", "banned"),
            ("nudity_clothing_conflicts", "conflict_2", "trigger"),
            ("nudity_clothing_conflicts", "conflict_2", "banned"),
            ("nudity_clothing_conflicts", "conflict_3", "trigger"),
            ("nudity_clothing_conflicts", "conflict_3", "banned"),
            # 3. material
            ("material_penetration", "material", "banned"),
            # 4. clothing
            ("clothing_style_state_coherence", "one_piece", "trigger"),
            ("clothing_style_state_coherence", "one_piece", "banned"),
            ("clothing_style_state_coherence", "pants", "trigger"),
            ("clothing_style_state_coherence", "pants", "banned"),
            # 5. gaze_angle
            ("gaze_angle_geometry", "high_angle", "angle"),
            ("gaze_angle_geometry", "high_angle", "banned"),
            ("gaze_angle_geometry", "low_angle", "angle"),
            ("gaze_angle_geometry", "low_angle", "banned"),
            ("gaze_angle_geometry", "pov", "angle"),
            # 6. gaze_mutual
            ("gaze_mutual_exclusion", "pair_0", "exclusive_a"),
            ("gaze_mutual_exclusion", "pair_0", "exclusive_b"),
            ("gaze_mutual_exclusion", "pair_1", "exclusive_a"),
            ("gaze_mutual_exclusion", "pair_1", "exclusive_b"),
            ("gaze_mutual_exclusion", "pair_2", "exclusive_a"),
            ("gaze_mutual_exclusion", "pair_2", "exclusive_b"),
            # 7. occlusion
            ("accessory_occlusion_gaze_coherence", "occlusion", "trigger"),
            ("accessory_occlusion_gaze_coherence", "occlusion", "banned"),
            # 8. framing
            ("framing_lower_body_coherence", "framing", "trigger"),
            ("framing_lower_body_coherence", "framing", "banned"),
            # 9. liquid
            ("liquid_restrictions", "cum_eyes", "trigger"),
            ("liquid_restrictions", "opaque_paint", "trigger"),
            ("liquid_restrictions", "pussy_juice", "trigger"),
            ("liquid_restrictions", "liquid_words", "liquid"),
            # 10. device
            ("device_quality_compatibility", "analog_film", "device"),
            ("device_quality_compatibility", "analog_film", "banned"),
            ("device_quality_compatibility", "cctv", "device"),
            ("device_quality_compatibility", "cctv", "banned"),
            ("device_quality_compatibility", "phone", "device"),
            ("device_quality_compatibility", "phone", "banned"),
            ("device_quality_compatibility", "webcam", "device"),
            ("device_quality_compatibility", "webcam", "banned"),
            # 11. tattoo
            ("tattoo_dermal_fusion", "tattoo", "tattoo"),
            # 12. pose
            ("pose_hand_occupation", "pose_hand", "trigger"),
            ("pose_hand_occupation", "pose_hand", "handheld"),
            # 13. handheld
            ("handheld_props_single_holder", "handheld", "handheld"),
            # 14. emotion
            ("emotion_gaze_affinity", "shy", "emotion"),
            ("emotion_gaze_affinity", "shy", "banned"),
            ("emotion_gaze_affinity", "bored", "emotion"),
            ("emotion_gaze_affinity", "bored", "banned"),
            # 15. lighting
            ("environmental_lighting_coherence", "daylight", "trigger"),
            ("environmental_lighting_coherence", "daylight", "banned"),
            # 16. monochrome
            ("monochrome_film_chroma_coherence", "monochrome", "trigger"),
            ("monochrome_film_chroma_coherence", "monochrome", "banned"),
            # 17. makeup
            ("makeup_details_coherence", "no_makeup", "trigger"),
            ("makeup_details_coherence", "no_makeup", "banned"),
        ]

        total_patterns_in_rules = 0
        all_pattern_identities = set()

        for r in raw_rules["rules"]:
            tf = r.get("text_fallback")
            if tf:
                for p in tf.get("patterns", []):
                    total_patterns_in_rules += 1
                    p_id = (r["id"], p["pattern"], p["match_mode"], p["group_id"], p["role"])
                    self.assertNotIn(p_id, all_pattern_identities, f"Duplicate pattern declaration: {p_id}")
                    all_pattern_identities.add(p_id)

        self.assertEqual(total_patterns_in_rules, 501, f"Expected 501 total fallback patterns, got {total_patterns_in_rules}")

        consumed_pattern_identities = set()
        for rid, gid, role in production_selectors:
            rule = rule_map[rid]
            pats = rule["text_fallback"]["patterns"]
            matched = [p for p in pats if p.get("group_id") == gid and p.get("role") == role]
            # 强断言每个生产 selector 至少消费 1 个 pattern
            self.assertGreaterEqual(len(matched), 1, f"Selector ({rid}, {gid}, {role}) consumed 0 patterns!")
            for p in matched:
                p_id = (rid, p["pattern"], p["match_mode"], gid, role)
                # 强断言两两互斥 (单射)
                self.assertNotIn(p_id, consumed_pattern_identities, f"Pattern {p_id} consumed by multiple selectors!")
                consumed_pattern_identities.add(p_id)

        # 强断言满射：全部 501 个 pattern 均被消费
        self.assertEqual(
            consumed_pattern_identities,
            all_pattern_identities,
            f"Unconsumed patterns: {all_pattern_identities - consumed_pattern_identities}"
        )
        self.assertEqual(len(consumed_pattern_identities), 501)


if __name__ == "__main__":
    unittest.main()
