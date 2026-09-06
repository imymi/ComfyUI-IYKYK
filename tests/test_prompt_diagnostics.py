"""
test_prompt_diagnostics.py — 提示词诊断节点、确定性审计 JSON 与 Draft-7 Schema 门禁测试
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from random import Random
from unittest.mock import patch

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from lib.conflict_resolver import ConflictResolver, is_formal_atom
from lib.models import (
    GenerationResult,
    PromptAtom,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    TagProvenance,
)
from lib.rule_contract import DAG_FROZEN_ORDER
import nodes
from nodes import (
    NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS,
    IYKYKCustomSlotCombiner,
    IYKYKPresetBrowser,
    IYKYKPromptDiagnostics,
    IYKYKPromptGenerator,
    _sampler,
    format_diagnostics_dict,
    format_diagnostics_json,
)
from tests.audit_oracle import validate_audit_json_oracle

SCHEMA_PATH = REPO_DIR / "schemas" / "diagnostics.schema.json"


class TestPromptDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = IYKYKPromptGenerator()
        cls.diagnostics = IYKYKPromptDiagnostics()
        cls.default_inputs = {
            k: v[1]["default"]
            for k, v in cls.generator.INPUT_TYPES()["required"].items()
        }
        cls.schema_doc = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        if HAS_JSONSCHEMA:
            jsonschema.Draft7Validator.check_schema(cls.schema_doc)
            cls.validator = jsonschema.Draft7Validator(cls.schema_doc)

    def _validate_audit(self, audit, *, expected_positive=None, trusted_inputs=None, schema=True):
        """Repository integration path: every successful Oracle run has trusted UI inputs."""
        return validate_audit_json_oracle(
            audit,
            schema_doc=self.schema_doc if schema else None,
            expected_positive=expected_positive,
            trusted_inputs=self.default_inputs if trusted_inputs is None else trusted_inputs,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 1. 节点接口契约与向后兼容性
    # ──────────────────────────────────────────────────────────────────────────

    def test_01_node_registration_and_signature(self):
        """断言第四个节点 IYKYKPromptDiagnostics 注册与签名契约。"""
        self.assertIn("IYKYKPromptDiagnostics", NODE_CLASS_MAPPINGS)
        self.assertIn("IYKYKPromptDiagnostics", NODE_DISPLAY_NAME_MAPPINGS)
        self.assertEqual(NODE_CLASS_MAPPINGS["IYKYKPromptDiagnostics"], IYKYKPromptDiagnostics)
        self.assertEqual(NODE_DISPLAY_NAME_MAPPINGS["IYKYKPromptDiagnostics"], "🔎 IYKYK 提示词诊断")

        # 完整复用主生成器 INPUT_TYPES
        self.assertEqual(IYKYKPromptDiagnostics.INPUT_TYPES(), IYKYKPromptGenerator.INPUT_TYPES())

        # 返回四个 STRING
        self.assertEqual(IYKYKPromptDiagnostics.RETURN_TYPES, ("STRING", "STRING", "STRING", "STRING"))
        self.assertEqual(
            IYKYKPromptDiagnostics.RETURN_NAMES,
            (
                "正面提示词 (STRING)",
                "负面提示词 (STRING)",
                "中文场景描述 (STRING)",
                "审计报告 JSON (STRING)",
            ),
        )
        self.assertEqual(IYKYKPromptDiagnostics.FUNCTION, "diagnose")
        self.assertEqual(IYKYKPromptDiagnostics.CATEGORY, "IYKYK / 提示词生成")

        # 缓存键一致性
        h_gen = IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42, **self.default_inputs)
        h_diag = IYKYKPromptDiagnostics.IS_CHANGED(prompt_seed=42, **self.default_inputs)
        self.assertEqual(h_gen, h_diag)

    def test_02_legacy_three_nodes_unchanged(self):
        """断言既有 3 个节点的输入、输出、函数名与返回类型严格未改变。"""
        self.assertEqual(IYKYKPromptGenerator.RETURN_TYPES, ("STRING", "STRING", "STRING"))
        self.assertEqual(IYKYKPromptGenerator.FUNCTION, "generate")

        self.assertEqual(IYKYKPresetBrowser.RETURN_TYPES, ("STRING", "STRING", "STRING"))
        self.assertEqual(IYKYKPresetBrowser.FUNCTION, "browse")

        self.assertEqual(IYKYKCustomSlotCombiner.RETURN_TYPES, ("STRING", "STRING", "STRING"))
        self.assertEqual(IYKYKCustomSlotCombiner.FUNCTION, "combine")

    def test_03_single_pure_function_call(self):
        """断言 diagnose 内部与主生成器调用同一纯函数一次；不发生重复采样或二次消解。"""
        with patch("nodes._generate_structured", wraps=nodes._generate_structured) as mock_gen:
            res = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=123)
            self.assertEqual(mock_gen.call_count, 1)
            self.assertEqual(len(res), 4)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. 前三个输出逐字节一致性与 77 预设 × (8 配方 + None) = 693 组全量验证
    # ──────────────────────────────────────────────────────────────────────────

    def test_04_fixed_seed_byte_for_byte_identity_random(self):
        """固定 seed 下，普通随机采样的前三个输出与主生成器逐字节一致。"""
        for seed in (0, 1, 42, 100, 9999):
            g_pos, g_neg, g_desc = self.generator.generate(**self.default_inputs, prompt_seed=seed)
            d_pos, d_neg, d_desc, d_audit = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=seed)
            self.assertEqual(g_pos, d_pos, f"Positive mismatch at seed {seed}")
            self.assertEqual(g_neg, d_neg, f"Negative mismatch at seed {seed}")
            self.assertEqual(g_desc, d_desc, f"Description mismatch at seed {seed}")

    def test_05_all_77_presets_x_9_recipes_693_matrix_parity(self):
        """主生成器与诊断节点执行 77 × (8 配方 + None) = 693 组全量测试并逐组比较前三个输出。"""
        presets = _sampler.list_preset_names()
        self.assertEqual(len(presets), 77, f"Expected 77 presets, got {len(presets)}")

        recipes = ["无 (None)"] + _sampler.list_style_recipes()
        self.assertEqual(len(recipes), 9, f"Expected 9 recipe options (8 + None), got {len(recipes)}")

        tested_count = 0
        seed = 42
        for p in presets:
            for r in recipes:
                inp = dict(self.default_inputs)
                inp["预设模板"] = p
                inp["风格配方"] = r
                g_pos, g_neg, g_desc = self.generator.generate(**inp, prompt_seed=seed)
                d_pos, d_neg, d_desc, d_audit = self.diagnostics.diagnose(**inp, prompt_seed=seed)

                self.assertEqual(g_pos, d_pos, f"[{p} | {r}] Positive prompt mismatch")
                self.assertEqual(g_neg, d_neg, f"[{p} | {r}] Negative prompt mismatch")
                self.assertEqual(g_desc, d_desc, f"[{p} | {r}] Chinese desc mismatch")
                self.assertGreater(len(g_pos), 0, f"[{p} | {r}] Positive prompt is empty")
                tested_count += 1

        self.assertEqual(tested_count, 693, f"Expected 693 tests, completed {tested_count}")

    # ──────────────────────────────────────────────────────────────────────────
    # 3. 确定性审计 JSON 序列化规范
    # ──────────────────────────────────────────────────────────────────────────

    def test_06_deterministic_serialization_invariants(self):
        """断言审计 JSON 满足 UTF-8、无换行、键排序、紧凑分隔符、无时间戳与 UUID。"""
        d_pos, d_neg, d_desc, d_audit = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)

        # 1. 结尾无换行
        self.assertFalse(d_audit.endswith("\n"), "Audit JSON must not end with newline")

        # 2. 紧凑分隔符 (无 ", " 或 ": ")
        self.assertNotIn(": ", d_audit, "Found whitespace after colon")
        self.assertNotIn(", ", d_audit, "Found whitespace after comma")

        # 3. 键字典序排序
        obj = json.loads(d_audit)
        top_keys = list(obj.keys())
        self.assertEqual(top_keys, sorted(top_keys), "Top-level keys not sorted")

        # 4. 8 个顶层字段
        expected_keys = [
            "context_profile",
            "counts",
            "decisions",
            "effective_seed",
            "rules_applied",
            "schema_version",
            "selections",
            "unresolved_conflicts",
        ]
        self.assertEqual(top_keys, expected_keys)
        self.assertEqual(obj["schema_version"], "1.0")
        self.assertEqual(obj["effective_seed"], 42)

        # 5. 无时间戳、内存地址、UUID、临时路径
        self.assertNotIn("0x", d_audit)
        self.assertNotIn("/tmp", d_audit)
        self.assertNotIn("timestamp", d_audit)

        # 6. UTF-8 汉字原生保留 (ensure_ascii=False，测试非 ASCII 字符不转义)
        mock_origin = SelectionOrigin(
            selector="clothing",
            selected_id="qipao",
            raw_value="旗袍写真",
            mode="explicit",
            parent_ids=("qipao",),
            entry_point="diagnostics",
        )
        mock_atom = PromptAtom(
            atom_id="atom_test_zh",
            text="旗袍写真",
            source_slot="clothing",
            span_type=SpanType.PLAIN,
            source_item_id="qipao",
            provenance=TagProvenance(
                item_id="qipao",
                parent_ids=("qipao",),
                semantic_ids=("clothing:qipao",),
            ),
            origin=mock_origin,
            id="qipao__001",
        )
        mock_res = GenerationResult(
            positive="旗袍写真",
            negative="",
            description="旗袍写真",
            atoms=(mock_atom,),
            rules_applied=(),
            source_atoms=(mock_atom,),
            effective_seed=42,
            selections=(mock_origin,),
        )
        zh_json = format_diagnostics_json(mock_res)
        self.assertIn("旗袍写真", zh_json)
        self.assertNotIn("\\u", zh_json)

    def test_07_context_profile_weights_quantization_and_negative_zero(self):
        """断言情境权重精确量化至小数点后 12 位并消除负零。"""
        _, _, _, d_audit = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        obj = json.loads(d_audit)
        cp = obj["context_profile"]
        if cp and "weights" in cp:
            for ctx, w in cp["weights"].items():
                self.assertIsInstance(w, (int, float))
                # 消除负零
                self.assertFalse(str(w).startswith("-0"), f"Found negative zero for {ctx}: {w}")
                # 检查小数位数 <= 12
                s = str(w)
                if "." in s:
                    decimals = len(s.split(".")[1])
                    self.assertLessEqual(decimals, 12, f"Weight {ctx}={w} exceeded 12 decimals")

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Schema 校验正向用例与测试 Oracle 跨字段闭环
    # ──────────────────────────────────────────────────────────────────────────

    def test_08_schema_positive_cases_and_oracle_closure(self):
        """验证常规采样、预设、配方与冲突用例全部通过 Schema，并由 Oracle 闭合图关系。"""
        test_cases = [
            ("default", self.default_inputs, 42),
            ("tattoo_inject", {**self.default_inputs, "纹身标记": _sampler.list_tattoo_styles()[0]}, 42),
            ("recipe_clash", {**self.default_inputs, "风格配方": _sampler.list_style_recipes()[0]}, 100),
            ("nudity_drop", {**self.default_inputs, "裸露等级": "L5 极致全裸 (Full Nude)", "服装款式": "旗袍 (Qipao/Cheongsam)"}, 7),
        ]

        for name, inps, seed in test_cases:
            g_pos, _, _ = self.generator.generate(**inps, prompt_seed=seed)
            d_pos, d_neg, d_desc, audit_str = self.diagnostics.diagnose(**inps, prompt_seed=seed)
            self.assertEqual(g_pos, d_pos, f"[{name}] Positive prompt mismatch")

            # 运行可复用 Fail-Closed Oracle 完整校验
            schema = self.schema_doc if HAS_JSONSCHEMA else None
            validate_audit_json_oracle(
                audit_str, schema_doc=schema, expected_positive=d_pos, trusted_inputs=inps
            )

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Schema 反向变异拦截 (Fail-Closed)
    # ──────────────────────────────────────────────────────────────────────────

    def test_09_schema_negative_mutations_fail_closed(self):
        """断言各类非法 JSON 结构、缺失必填、多余字段与动作枚举均被 Schema 严厉拒绝。"""
        if not HAS_JSONSCHEMA:
            self.skipTest("jsonschema not installed")

        _, _, _, audit_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        base = json.loads(audit_str)

        # 变异 1: 缺少顶层必填字段 counts
        m1 = copy.deepcopy(base)
        del m1["counts"]
        self.assertTrue(any("counts" in e.message for e in self.validator.iter_errors(m1)))

        # 变异 2: 多出顶层未知字段
        m2 = copy.deepcopy(base)
        m2["rogue_ninth_field"] = "leak"
        self.assertTrue(any("rogue_ninth_field" in e.message for e in self.validator.iter_errors(m2)))

        # 变异 3: 非法 schema_version
        m3 = copy.deepcopy(base)
        m3["schema_version"] = "2.0"
        self.assertTrue(any("schema_version" in str(e) for e in self.validator.iter_errors(m3)))

        # 变异 4: 负数计数
        m4 = copy.deepcopy(base)
        m4["counts"]["dropped"] = -1
        self.assertTrue(any("dropped" in str(e) for e in self.validator.iter_errors(m4)))

        # 变异 5: counts 内多余字段
        m5 = copy.deepcopy(base)
        m5["counts"]["extra"] = 0
        self.assertTrue(any("extra" in str(e) for e in self.validator.iter_errors(m5)))

        # 变异 6: selections 内未知 entry_point
        m6 = copy.deepcopy(base)
        m6["selections"][0]["entry_point"] = "rogue_entry"
        self.assertTrue(any("rogue_entry" in str(e) for e in self.validator.iter_errors(m6)))

        # 变异 7: drop 动作带有非 null after_text (违反 drop 规则)
        m7 = copy.deepcopy(base)
        m7["decisions"] = [{
            "action": "drop",
            "after_text": "should_be_null",
            "before_text": "tag",
            "decision_id": "dec_01",
            "parent_source_ids": ["atom_01"],
            "phase": "anchors",
            "produced_atom_ids": [],
            "reason_code": "MUTEX",
            "rule_id": "spatial_environmental_mutual_exclusion",
            "sequence": 1,
            "target_atom_id": "atom_02",
            "winner_atom_ids": ["atom_01"],
        }]
        self.assertTrue(len(list(self.validator.iter_errors(m7))) > 0)

        # 变异 8: replace 动作带有 null after_text (违反 replace 规则)
        m8 = copy.deepcopy(base)
        m8["decisions"] = [{
            "action": "replace",
            "after_text": None,
            "before_text": "tag",
            "decision_id": "dec_02",
            "parent_source_ids": ["atom_01"],
            "phase": "physical",
            "produced_atom_ids": ["atom_new"],
            "reason_code": "MUTEX",
            "rule_id": "material_penetration",
            "sequence": 1,
            "target_atom_id": "atom_02",
            "winner_atom_ids": [],
        }]
        self.assertTrue(len(list(self.validator.iter_errors(m8))) > 0)

        # 变异 9: inject 动作带有非 null target_atom_id (违反 inject 规则)
        m9 = copy.deepcopy(base)
        m9["decisions"] = [{
            "action": "inject",
            "after_text": "injected",
            "before_text": None,
            "decision_id": "dec_03",
            "parent_source_ids": ["atom_01"],
            "phase": "effects",
            "produced_atom_ids": ["atom_inj"],
            "reason_code": "MUTEX",
            "rule_id": "tattoo_dermal_fusion",
            "sequence": 1,
            "target_atom_id": "atom_target_invalid",
            "winner_atom_ids": ["atom_01"],
        }]
        self.assertTrue(len(list(self.validator.iter_errors(m9))) > 0)

        # 变异 10: 非法 rule_id
        m10 = copy.deepcopy(base)
        m10["rules_applied"] = ["non_existent_rule_999"]
        self.assertTrue(any("non_existent_rule_999" in str(e) for e in self.validator.iter_errors(m10)))

    # ──────────────────────────────────────────────────────────────────────────
    # 6. 跨进程与跨 PYTHONHASHSEED 确定性复现
    # ──────────────────────────────────────────────────────────────────────────

    def test_10_cross_process_pythonhashseed_determinism(self):
        """断言在不同 PYTHONHASHSEED 独立子进程中，提示词输出与审计 JSON 完全逐字节一致。"""
        test_code = """
import json
from nodes import IYKYKPromptDiagnostics
d = IYKYKPromptDiagnostics()
inp = {k: v[1]['default'] for k, v in d.INPUT_TYPES()['required'].items()}
pos, neg, desc, audit = d.diagnose(**inp, prompt_seed=12345)
print(pos)
print("---SPLIT---")
print(neg)
print("---SPLIT---")
print(desc)
print("---SPLIT---")
print(audit)
"""
        outputs = []
        for hash_seed in ("0", "42", "999999"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = hash_seed
            res = subprocess.run(
                [sys.executable, "-c", test_code],
                cwd=REPO_DIR,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            outputs.append(res.stdout)

        self.assertEqual(outputs[0], outputs[1], "Output drifted between PYTHONHASHSEED=0 and 42")
        self.assertEqual(outputs[0], outputs[2], "Output drifted between PYTHONHASHSEED=0 and 999999")

    # ──────────────────────────────────────────────────────────────────────────
    # 7. Fail-Closed Oracle 故障注入与回归用例 (R3-P1-001, R3-P1-002, R3-P2-001)
    # ──────────────────────────────────────────────────────────────────────────

    def test_11_oracle_catches_tampered_counts(self):
        """断言 Oracle 严厉拒绝篡改的计数与破坏守恒方程的审计 JSON。"""
        _, _, _, audit_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        base = json.loads(audit_str)

        # 篡改 accepted_atoms 计数
        m1 = copy.deepcopy(base)
        m1["counts"]["accepted_atoms"] += 1
        with self.assertRaises(AssertionError):
            validate_audit_json_oracle(m1, trusted_inputs=self.default_inputs)

        # 篡改 source_atoms 计数
        m2 = copy.deepcopy(base)
        m2["counts"]["source_atoms"] -= 1
        with self.assertRaises(AssertionError):
            validate_audit_json_oracle(m2, trusted_inputs=self.default_inputs)

    def test_12_oracle_catches_fake_rule_id(self):
        """断言 Oracle 严厉拒绝决策中包含未在 rules_applied 登记的 rule_id (保持计数守恒)。"""
        _, _, _, audit_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        base = json.loads(audit_str)

        m = copy.deepcopy(base)
        target = m["selections"][0]["source_atoms"][0]
        fake_decision = {
            "action": "drop",
            "after_text": None,
            "before_text": target["text"],
            "decision_id": "dec_fake_001",
            "parent_source_ids": [target["atom_id"]],
            "phase": "anchors",
            "produced_atom_ids": [],
            "reason_code": "MUTEX",
            "rule_id": "spatial_environmental_mutual_exclusion",
            "sequence": len(m["decisions"]),
            "target_atom_id": target["atom_id"],
            "winner_atom_ids": [],
        }
        m["decisions"].append(fake_decision)
        # 维持计数守恒与非目标约束有效
        m["counts"]["dropped"] += 1
        m["counts"]["accepted_atoms"] -= 1
        m["selections"][0]["accepted_atoms"] = [
            a for a in m["selections"][0]["accepted_atoms"] if a["atom_id"] != target["atom_id"]
        ]
        target["is_accepted"] = False
        with self.assertRaisesRegex(AssertionError, r"rules_applied mismatch"):
            validate_audit_json_oracle(m, schema_doc=self.schema_doc, trusted_inputs=self.default_inputs)

    def test_13_oracle_catches_broken_parent_link(self):
        """断言 Oracle 严厉拒绝指向越界 parent_source_id 的断链决策 (保持计数守恒)。"""
        _, _, _, audit_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        base = json.loads(audit_str)

        m = copy.deepcopy(base)
        target = m["selections"][0]["source_atoms"][0]
        phantom_id = "atom_phantom_999999"
        valid_rule = m["rules_applied"][0] if m["rules_applied"] else "spatial_environmental_mutual_exclusion"
        fake_decision = {
            "action": "drop",
            "after_text": None,
            "before_text": target["text"],
            "decision_id": "dec_broken_001",
            "parent_source_ids": [phantom_id],
            "phase": "anchors",
            "produced_atom_ids": [],
            "reason_code": "MUTEX",
            "rule_id": valid_rule,
            "sequence": len(m["decisions"]),
            "target_atom_id": target["atom_id"],
            "winner_atom_ids": [],
        }
        m["decisions"].append(fake_decision)
        m["counts"]["dropped"] += 1
        m["counts"]["accepted_atoms"] -= 1
        m["selections"][0]["accepted_atoms"] = [
            a for a in m["selections"][0]["accepted_atoms"] if a["atom_id"] != target["atom_id"]
        ]
        target["is_accepted"] = False
        if not m["rules_applied"]:
            m["rules_applied"].append(valid_rule)
        with self.assertRaisesRegex(AssertionError, r"was not known before the decision"):
            validate_audit_json_oracle(m, schema_doc=self.schema_doc, trusted_inputs=self.default_inputs)

    def test_14_oracle_catches_duplicate_atom_ids(self):
        """断言 Oracle 严厉拒绝源原子或产出原子中存在的重复 ID (保持计数守恒与顺序有效)。"""
        _, _, _, audit_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        base = json.loads(audit_str)

        m = copy.deepcopy(base)
        # 复制末尾原子并维持 tag_order / span_order
        dup_atom = copy.deepcopy(m["selections"][0]["source_atoms"][-1])
        m["selections"][0]["source_atoms"].append(dup_atom)
        m["counts"]["source_atoms"] += 1
        m["counts"]["budget_filtered"] += 1
        m["selections"][0]["budget_filtered_records"].append({
            "atom_id": dup_atom["atom_id"],
            "candidate_words": 1,
            "used_words": 50,
            "word_budget": 50,
            "reason": "word_budget_exceeded",
        })
        with self.assertRaisesRegex(AssertionError, r"Duplicate source atom IDs detected"):
            validate_audit_json_oracle(m, schema_doc=self.schema_doc, trusted_inputs=self.default_inputs)

    def test_15_oracle_catches_duplicate_selection_records(self):
        """断言 Oracle 严厉拒绝针对同一选择项产生多条 selection 记录 (保持原子计数不变)。"""
        _, _, _, audit_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        base = json.loads(audit_str)

        m = copy.deepcopy(base)
        first_sel = copy.deepcopy(m["selections"][0])
        first_sel["source_atoms"] = []
        first_sel["accepted_atoms"] = []
        first_sel["produced_atoms"] = []
        first_sel["deduplicated_records"] = []
        first_sel["budget_filtered_records"] = []
        m["selections"].append(first_sel)
        with self.assertRaisesRegex(AssertionError, r"Duplicate selection record detected"):
            validate_audit_json_oracle(m, schema_doc=self.schema_doc, trusted_inputs=self.default_inputs)

    # ──────────────────────────────────────────────────────────────────────────
    # 8. 审核报告 8.5.4 具名负向变异测试用例 (C2-N01 ~ C2-N12)
    # ──────────────────────────────────────────────────────────────────────────

    def test_c2_n01_swap_counts_rejected_by_oracle(self):
        """C2-N01: 预设 C01 / seed 42 把全部 deduplicated 转为 budget_filtered 破坏分区证据。"""
        preset_name = _sampler.list_preset_names()[0]
        trusted = {**self.default_inputs, "预设模板": preset_name}
        pos, _, _, j_str = self.diagnostics.diagnose(**trusted, prompt_seed=42)
        audit = json.loads(j_str)
        self.assertGreater(audit["counts"]["deduplicated"], 0)
        audit["counts"]["budget_filtered"] += audit["counts"]["deduplicated"]
        audit["counts"]["deduplicated"] = 0
        with self.assertRaisesRegex(AssertionError, r"counts\.deduplicated .* != actual deduplicated records"):
            self._validate_audit(audit, expected_positive=pos, trusted_inputs=trusted)

    def test_c2_n02_accepted_atom_unknown_parent_rejected(self):
        """C2-N02: default / seed 42 accepted Atom 的 parent_ids 改成 unknown_parent 造成断链。"""
        pos, _, _, j_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        audit = json.loads(j_str)
        audit["selections"][0]["accepted_atoms"][0]["parent_ids"] = ["unknown_parent"]
        with self.assertRaisesRegex(AssertionError, r"payload mismatch with registered snapshot for field 'parent_ids'"):
            self._validate_audit(audit, expected_positive=pos)

    def test_c2_n03_accepted_atom_unknown_source_item_id_rejected(self):
        """C2-N03: default / seed 42 accepted Atom 的 source_item_id 改成 unknown_item 破坏快照一致性。"""
        pos, _, _, j_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        audit = json.loads(j_str)
        audit["selections"][0]["accepted_atoms"][0]["source_item_id"] = "unknown_item"
        with self.assertRaisesRegex(AssertionError, r"payload mismatch with registered snapshot for field 'source_item_id'"):
            self._validate_audit(audit, expected_positive=pos)

    def test_c2_n04_hide_final_atom_rejected(self):
        """C2-N04: default / seed 42 删除首个最终 Atom 伪造预算过滤并漏报实际 positive。"""
        pos, _, _, j_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        audit = json.loads(j_str)
        s = next(s for s in audit["selections"] if s["accepted_atoms"])
        atom = s["accepted_atoms"].pop(0)
        for x in [a for sel in audit["selections"] for a in sel["source_atoms"]]:
            if x["atom_id"] == atom["atom_id"]:
                x["is_accepted"] = False
        audit["counts"]["accepted_atoms"] -= 1
        audit["counts"]["budget_filtered"] += 1
        with self.assertRaisesRegex(AssertionError, r"Final active atoms do not match accepted_atoms"):
            self._validate_audit(audit, expected_positive=pos)

    def test_c2_n05_invalid_raw_value_rejected(self):
        """C2-N05: default / seed 42 raw_value 改成不存在于实际 UI 输入的文字。"""
        pos, _, _, j_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        audit = json.loads(j_str)
        audit["selections"][0]["raw_value"] = "not_the_original_UI_input"
        with self.assertRaisesRegex(AssertionError, r"does not match trusted input"):
            self._validate_audit(audit, expected_positive=pos)

    def test_c2_n06_replace_before_text_tampered_rejected(self):
        """C2-N06: wet / seed 1 改写首条 replace 的 before_text 未与目标快照比对。"""
        trusted = {**self.default_inputs, "服装状态": "湿身紧贴透光 (Wet & Clinging)"}
        pos, _, _, j_str = self.diagnostics.diagnose(**trusted, prompt_seed=1)
        audit = json.loads(j_str)
        self.assertGreater(len(audit["decisions"]), 0)
        audit["decisions"][0]["before_text"] = "not_the_target_text"
        with self.assertRaisesRegex(AssertionError, r"before_text 'not_the_target_text' != target atom text"):
            self._validate_audit(audit, expected_positive=pos, trusted_inputs=trusted)

    def test_c2_n07_replace_after_text_tampered_rejected(self):
        """C2-N07: wet / seed 1 改写首条 replace 的 after_text 未与产生物快照比对。"""
        trusted = {**self.default_inputs, "服装状态": "湿身紧贴透光 (Wet & Clinging)"}
        pos, _, _, j_str = self.diagnostics.diagnose(**trusted, prompt_seed=1)
        audit = json.loads(j_str)
        self.assertGreater(len(audit["decisions"]), 0)
        audit["decisions"][0]["after_text"] = "not_the_produced_text"
        with self.assertRaisesRegex(AssertionError, r"after_text 'not_the_produced_text' != produced atom text"):
            self._validate_audit(audit, expected_positive=pos, trusted_inputs=trusted)

    def test_c2_n08_duplicate_consumption_target_rejected(self):
        """C2-N08: wet / seed 1 第二条 replace 再次消费第一条的 target 重复消费。"""
        trusted = {**self.default_inputs, "服装状态": "湿身紧贴透光 (Wet & Clinging)"}
        pos, _, _, j_str = self.diagnostics.diagnose(**trusted, prompt_seed=1)
        audit = json.loads(j_str)
        self.assertGreaterEqual(len(audit["decisions"]), 2)
        audit["decisions"][1]["target_atom_id"] = audit["decisions"][0]["target_atom_id"]
        with self.assertRaisesRegex(AssertionError, r"was already consumed"):
            self._validate_audit(audit, expected_positive=pos, trusted_inputs=trusted)

    def test_c2_n09_reverse_accepted_atoms_order_rejected(self):
        """C2-N09: default / seed 42 反转各 selection 的 accepted_atoms 数组破坏业务顺序。"""
        pos, _, _, j_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        audit = json.loads(j_str)
        for s in audit["selections"]:
            s["accepted_atoms"].reverse()
        with self.assertRaisesRegex(AssertionError, r"accepted_atoms ordering violated"):
            self._validate_audit(audit, expected_positive=pos)

    def test_c2_n10_rules_applied_unproduced_rule_appended_rejected(self):
        """C2-N10: default / seed 42 rules_applied 追加未产生 decision 的规则。"""
        pos, _, _, j_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        audit = json.loads(j_str)
        audit["rules_applied"].append("gaze_mutual_exclusion")
        with self.assertRaisesRegex(AssertionError, r"rules_applied mismatch"):
            self._validate_audit(audit, expected_positive=pos)

    def test_c2_n11_unresolved_conflicts_not_empty_rejected(self):
        """C2-N11: default / seed 42 填入非空 unresolved_conflicts 虚假声称硬冲突残留。"""
        pos, _, _, j_str = self.diagnostics.diagnose(**self.default_inputs, prompt_seed=42)
        audit = json.loads(j_str)
        audit["unresolved_conflicts"] = [["gaze_mutual_exclusion", "x", "y"]]
        with self.assertRaisesRegex(AssertionError, r"Successful generation report must have empty unresolved_conflicts"):
            self._validate_audit(audit, expected_positive=pos)

    def test_c2_n12_resurrect_consumed_atom_rejected(self):
        """C2-N12: wet / seed 1 把保留 produced ID 改回被 replace 的 target ID 企图死者复活。"""
        trusted = {**self.default_inputs, "服装状态": "湿身紧贴透光 (Wet & Clinging)"}
        pos, _, _, j_str = self.diagnostics.diagnose(**trusted, prompt_seed=1)
        audit = json.loads(j_str)
        d = audit["decisions"][0]
        pid, tid = d["produced_atom_ids"][0], d["target_atom_id"]
        accepted_list = [x for s in audit["selections"] for x in s["accepted_atoms"]]
        sources_list = [x for s in audit["selections"] for x in s["source_atoms"]]
        next(x for x in accepted_list if x["atom_id"] == pid)["atom_id"] = tid
        next(x for x in sources_list if x["atom_id"] == tid)["is_accepted"] = True
        with self.assertRaisesRegex(AssertionError, r"payload mismatch with registered snapshot for field 'text'"):
            self._validate_audit(audit, expected_positive=pos, trusted_inputs=trusted)

    # ──────────────────────────────────────────────────────────────────────────
    # 9. 回归测试套件 (R3-P1-001, R3-P1-002, R3-P1-005, R3-P2-001)
    # ──────────────────────────────────────────────────────────────────────────

    def test_16_regression_qipao_single_selection_and_full_raw_value(self):
        """回归 R3-P1-001: 旗袍显式选择只能有一条 clothing selection，raw_value 等于完整 UI 输入。"""
        inp = {**self.default_inputs, "服装款式": "旗袍 (Qipao/Cheongsam)"}
        g_pos, g_neg, g_zh = self.generator.generate(**inp, prompt_seed=42)
        d_pos, d_neg, d_zh, d_audit = self.diagnostics.diagnose(**inp, prompt_seed=42)

        self.assertEqual(g_pos, d_pos)
        self.assertEqual(g_neg, d_neg)
        self.assertEqual(g_zh, d_zh)

        schema = self.schema_doc if HAS_JSONSCHEMA else None
        audit = validate_audit_json_oracle(
            d_audit, schema_doc=schema, expected_positive=d_pos, trusted_inputs=inp
        )

        clothing_sels = [s for s in audit["selections"] if s["selector"] == "clothing"]
        self.assertEqual(len(clothing_sels), 1, f"Expected exactly 1 clothing selection, got {len(clothing_sels)}")
        self.assertEqual(clothing_sels[0]["raw_value"], "旗袍 (Qipao/Cheongsam)")
        self.assertGreater(len(clothing_sels[0]["source_atoms"]), 0)

    def test_17_regression_wet_clothing_seed_1_replace_path_closed(self):
        """回归 R3-P1-002: 湿身紧贴透光 + seed=1 的 replace 路径完全闭环且数学守恒。"""
        inp = {**self.default_inputs, "服装状态": "湿身紧贴透光 (Wet & Clinging)"}
        g_pos, g_neg, g_zh = self.generator.generate(**inp, prompt_seed=1)
        d_pos, d_neg, d_zh, d_audit = self.diagnostics.diagnose(**inp, prompt_seed=1)

        self.assertEqual(g_pos, d_pos)
        self.assertEqual(g_neg, d_neg)
        self.assertEqual(g_zh, d_zh)

        schema = self.schema_doc if HAS_JSONSCHEMA else None
        audit = validate_audit_json_oracle(
            d_audit, schema_doc=schema, expected_positive=d_pos, trusted_inputs=inp
        )
        self.assertGreater(len(audit["decisions"]), 0)

    def test_18_regression_custom_combiner_whitespace_preservation(self):
        """回归: 自定义槽位拼接器保留 raw_value 首尾空白并产生正确的原子与正向提示词。"""
        combiner = IYKYKCustomSlotCombiner()
        raw_val = "  plain cotton jacket, wooden buttons  "
        res = combiner.combine_structured(服装款式=raw_val, prompt_seed=42)
        self.assertEqual(len(res.selections), 1)
        self.assertEqual(res.selections[0].raw_value, raw_val)
        self.assertEqual(res.positive, "plain cotton jacket, wooden buttons")
        audit_json = format_diagnostics_json(res)
        audit = json.loads(audit_json)
        self.assertEqual(audit["selections"][0]["raw_value"], raw_val)
        validate_audit_json_oracle(
            audit, schema_doc=self.schema_doc, expected_positive=res.positive,
            trusted_inputs={"服装款式": raw_val},
        )

    def test_19_regression_resolver_origin_mode(self):
        """回归 R3-P1-005: 冲突消解产出的 Atom 使用 mode='resolver'，并完整链接回 target/parent。"""
        inp = {**self.default_inputs, "服装状态": "湿身紧贴透光 (Wet & Clinging)"}
        d_pos, _, _, d_audit = self.diagnostics.diagnose(**inp, prompt_seed=1)
        audit = json.loads(d_audit)
        resolver_sels = [s for s in audit["selections"] if s["mode"] == "resolver"]
        self.assertGreater(len(resolver_sels), 0)
        for r_sel in resolver_sels:
            self.assertEqual(r_sel["mode"], "resolver")
            self.assertTrue(r_sel["selected_id"].startswith("rule:"))
            self.assertGreater(len(r_sel["produced_atoms"]), 0)
            self.assertEqual(r_sel["source_atoms"], [])
        validate_audit_json_oracle(
            audit, schema_doc=self.schema_doc, expected_positive=d_pos, trusted_inputs=inp
        )

    def test_20_regression_word_budget_zero_and_boundary_truncation(self):
        """回归 R3-P1-002: 词数限制 0 与边界截断时整 Tag 不拆分并产生完整的 budget_filter_records。"""
        from lib.assembler import assemble_result
        from lib.models import PromptFragment
        origin = SelectionOrigin(
            entry_point="custom_combiner", mode="custom", selector="custom", raw_value="tag one, tag two, tag three"
        )
        frags = [
            PromptFragment(text="tag one", source_slot="custom", order=0, origin=origin, provenance=TagProvenance(kind="custom")),
            PromptFragment(text="tag two", source_slot="custom", order=1, origin=origin, provenance=TagProvenance(kind="custom")),
            PromptFragment(text="tag three", source_slot="custom", order=2, origin=origin, provenance=TagProvenance(kind="custom")),
        ]
        # max_words = 0
        res0 = assemble_result(frags, REPO_DIR / "data", max_words=0)
        self.assertEqual(res0.prompt, "")
        self.assertEqual(len(res0.accepted_atoms), 0)
        self.assertEqual(len(res0.budget_filter_records), 3)
        gen0 = GenerationResult(
            positive="", negative="", description="",
            atoms=res0.accepted_atoms,
            rules_applied=res0.rules_applied,
            source_atoms=res0.source_atoms,
            selections=(origin,),
            budget_filtered_atoms=res0.budget_filtered_atoms,
            budget_filter_records=res0.budget_filter_records,
        )
        validate_audit_json_oracle(
            format_diagnostics_json(gen0), schema_doc=self.schema_doc, expected_positive="",
            trusted_inputs={"custom": origin.raw_value},
        )

        # max_words = 2 (边界截断)
        res2 = assemble_result(frags, REPO_DIR / "data", max_words=2)
        self.assertEqual(res2.prompt, "tag one")
        self.assertEqual(len(res2.accepted_atoms), 1)
        self.assertEqual(len(res2.budget_filter_records), 2)
        gen2 = GenerationResult(
            positive="tag one", negative="", description="",
            atoms=res2.accepted_atoms,
            rules_applied=res2.rules_applied,
            source_atoms=res2.source_atoms,
            selections=(origin,),
            budget_filtered_atoms=res2.budget_filtered_atoms,
            budget_filter_records=res2.budget_filter_records,
        )
        validate_audit_json_oracle(
            format_diagnostics_json(gen2), schema_doc=self.schema_doc, expected_positive="tag one",
            trusted_inputs={"custom": origin.raw_value},
        )

    def test_21_c4_identity_roots_and_leaf_ids_fail_closed(self):
        """C4 review: changing both source and accepted identity snapshots cannot forge authority."""
        pos, _, _, audit_json = self.diagnostics.diagnose(
            **self.default_inputs, prompt_seed=42
        )
        base = json.loads(audit_json)
        source = next(
            atom for selection in base["selections"] for atom in selection["source_atoms"]
            if atom["is_accepted"]
        )
        accepted = next(
            atom for selection in base["selections"] for atom in selection["accepted_atoms"]
            if atom["atom_id"] == source["atom_id"]
        )

        forged_leaf = copy.deepcopy(base)
        f_source = next(
            atom for selection in forged_leaf["selections"] for atom in selection["source_atoms"]
            if atom["atom_id"] == source["atom_id"]
        )
        f_accepted = next(
            atom for selection in forged_leaf["selections"] for atom in selection["accepted_atoms"]
            if atom["atom_id"] == source["atom_id"]
        )
        f_source["id"] = f_accepted["id"] = "forged-leaf-id"
        with self.assertRaisesRegex(AssertionError, r"non-authoritative leaf id"):
            self._validate_audit(forged_leaf, expected_positive=pos)

        forged_root = copy.deepcopy(base)
        r_source = next(
            atom for selection in forged_root["selections"] for atom in selection["source_atoms"]
            if atom["atom_id"] == source["atom_id"]
        )
        r_accepted = next(
            atom for selection in forged_root["selections"] for atom in selection["accepted_atoms"]
            if atom["atom_id"] == source["atom_id"]
        )
        r_source["source_item_id"] = r_accepted["source_item_id"] = "forged_source_item"
        with self.assertRaisesRegex(AssertionError, r"non-authoritative source_item_id"):
            self._validate_audit(forged_root, expected_positive=pos)

        self.assertEqual(accepted["id"], source["id"])

    def test_22_c4_trusted_input_is_mandatory(self):
        """C4 review: plausible raw-value forgery is rejected without an external trust root."""
        _, _, _, audit_json = self.diagnostics.diagnose(
            **self.default_inputs, prompt_seed=42
        )
        with self.assertRaisesRegex(AssertionError, r"trusted_inputs is required"):
            validate_audit_json_oracle(audit_json, schema_doc=self.schema_doc)

    def test_23_c4_future_winner_and_parent_references_rejected(self):
        """C4 review: decision replay may reference only atoms available at that sequence."""
        trusted = {**self.default_inputs, "服装状态": "湿身紧贴透光 (Wet & Clinging)"}
        pos, _, _, audit_json = self.diagnostics.diagnose(**trusted, prompt_seed=1)
        base = json.loads(audit_json)
        self.assertGreaterEqual(len(base["decisions"]), 2)
        future_id = base["decisions"][1]["produced_atom_ids"][0]

        forged_winner = copy.deepcopy(base)
        forged_winner["decisions"][0]["winner_atom_ids"] = [future_id]
        with self.assertRaisesRegex(AssertionError, r"was not active before the decision"):
            self._validate_audit(
                forged_winner, expected_positive=pos, trusted_inputs=trusted
            )

        forged_parent = copy.deepcopy(base)
        forged_parent["decisions"][0]["parent_source_ids"] = [future_id]
        with self.assertRaisesRegex(AssertionError, r"was not known before the decision"):
            self._validate_audit(
                forged_parent, expected_positive=pos, trusted_inputs=trusted
            )

        all_random = dict(self.default_inputs)
        for key in (
            "饰品头饰", "妆容细节", "胶片风格", "液体效果", "纹身标记",
            "道具物件", "角色设定", "真实微瑕",
        ):
            all_random[key] = "随机 (Random)"
        active_pos, _, _, active_audit = self.diagnostics.diagnose(
            **all_random, prompt_seed=1471
        )
        self._validate_audit(
            active_audit, expected_positive=active_pos, trusted_inputs=all_random
        )

    def test_24_c4_budget_numbers_are_replayed_from_tag_order(self):
        """C4 review: self-consistent-looking but false budget arithmetic is rejected."""
        from lib.assembler import assemble_result
        from lib.models import PromptFragment

        raw_value = "boundary fixture"
        origin = SelectionOrigin(
            entry_point="custom_combiner", mode="custom", selector="custom", raw_value=raw_value
        )
        first = " ".join(f"w{i}" for i in range(249))
        fragments = [
            PromptFragment(text=first, source_slot="custom", order=0, origin=origin),
            PromptFragment(text="overflow word", source_slot="custom", order=1, origin=origin),
        ]
        assembled = assemble_result(fragments, REPO_DIR / "data", max_words=250)
        generated = GenerationResult(
            positive=assembled.prompt,
            negative="",
            description="",
            atoms=assembled.accepted_atoms,
            rules_applied=assembled.rules_applied,
            source_atoms=assembled.source_atoms,
            selections=(origin,),
            budget_filtered_atoms=assembled.budget_filtered_atoms,
            budget_filter_records=assembled.budget_filter_records,
        )
        audit = format_diagnostics_dict(generated)
        record = audit["selections"][0]["budget_filtered_records"][0]
        self.assertEqual(
            (record["word_budget"], record["used_words"], record["candidate_words"]),
            (250, 249, 2),
        )
        forged_numbers = copy.deepcopy(audit)
        forged_numbers["selections"][0]["budget_filtered_records"][0].update(
            {"word_budget": 1, "used_words": 0, "candidate_words": 1}
        )
        with self.assertRaisesRegex(AssertionError, r"Accepted tag exceeds replayed budget"):
            validate_audit_json_oracle(
                forged_numbers, trusted_inputs={"custom": raw_value}
            )

        forged_reason = copy.deepcopy(audit)
        forged_reason["selections"][0]["budget_filtered_records"][0]["reason"] = "plausible_but_false"
        with self.assertRaisesRegex(AssertionError, r"invalid reason"):
            validate_audit_json_oracle(
                forged_reason, trusted_inputs={"custom": raw_value}
            )

    def test_25_c4_source_mode_closed_enum_and_formality(self):
        """C4 review: only immutable original source modes may establish formal provenance."""
        for bad in ("", "typo", "resolver"):
            with self.subTest(source_mode=bad), self.assertRaises(ValueError):
                TagProvenance(source_mode=bad)

        resolver_origin = SelectionOrigin(
            entry_point="generator",
            mode="resolver",
            selector="custom",
            selected_id="rule:fixture",
            raw_value="fixture",
        )
        nonformal = PromptAtom(
            text="fixture",
            span_type=SpanType.PLAIN,
            source_slot="custom",
            provenance=TagProvenance(source_mode="custom"),
            facts=SemanticFacts(space_kind="indoor"),
            origin=resolver_origin,
        )
        self.assertFalse(is_formal_atom(nonformal))
        self.assertTrue(
            is_formal_atom(
                PromptAtom(
                    text="fixture",
                    span_type=SpanType.PLAIN,
                    source_slot="scene",
                    provenance=TagProvenance(source_mode="random"),
                    origin=resolver_origin,
                )
            )
        )

    def test_26_c4_mixed_protected_tag_selection_ownership_closed(self):
        """C4 review: retained protected source and replacement stay in their own audit owners."""
        raw_value = "spinning room <lora:foo:1.0>"
        result = IYKYKCustomSlotCombiner().combine_structured(
            场景主题=raw_value, prompt_seed=42
        )
        audit = format_diagnostics_dict(result)
        custom_sel = next(s for s in audit["selections"] if s["mode"] == "custom")
        resolver_sel = next(s for s in audit["selections"] if s["mode"] == "resolver")
        self.assertEqual(len(custom_sel["source_atoms"]), 2)
        self.assertEqual(len(custom_sel["accepted_atoms"]), 1)
        self.assertEqual(custom_sel["accepted_atoms"][0]["text"], "<lora:foo:1.0>")
        self.assertEqual(len(resolver_sel["produced_atoms"]), 1)
        self.assertEqual(len(resolver_sel["accepted_atoms"]), 1)
        validate_audit_json_oracle(
            audit,
            expected_positive=result.positive,
            trusted_inputs={"场景主题": raw_value},
        )


if __name__ == "__main__":
    unittest.main()
