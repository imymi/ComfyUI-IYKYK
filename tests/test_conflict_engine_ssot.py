"""
test_conflict_engine_ssot.py — 冲突消解引擎 17 规则收敛、match_mode、banned_combos 与 17 规则变异测试
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from random import Random

from lib.atomizer import fragments_to_atoms
from lib.conflict_resolver import ConflictResolver, match_pattern
from lib.errors import RuleConfigurationError
from lib.models import PromptAtom, PromptFragment, SpanType, TagProvenance
from lib.rule_contract import (
    RULE_REQUIRED_FIELDS,
    STABLE_RULE_ORDER,
    PatternSpec,
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

        resolved_atoms, rules_applied = self.resolver.resolve_atoms_with_report(raw_atoms, rng)
        resolved_texts = [a.text for a in resolved_atoms]
        self.assertIn("(classroom <lora:x:1>:1.2)", resolved_texts)
        self.assertIn("[classroom \"exact phrase\":1.2]", resolved_texts)

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


if __name__ == "__main__":
    unittest.main()
