#!/usr/bin/env python3
"""
tests/test_m5_3_audit_engine.py
M5-3 审计与因果归因引擎核心断言与十二大负向变异测试门禁 (v3.0 严谨因果版)

覆盖核心验证与反向攻击防护：
1. 正向验证：同一种子（seed=42）跨版本真实样本（Baseline f1b873c vs Target 1db9e67），通过因果证明，is_explained == True；
2. 负向变异 1：rules_data 传入空字典，消解决策依据丢失，必须 Fail-Closed 触发 UNEXPLAINED_MISSING_RULE_EVIDENCE；
3. 负向变异 2：affinity_data 传入空字典，亲和矩阵依据丢失，必须 Fail-Closed 触发 UNEXPLAINED_NO_AFFINITY_EVIDENCE；
4. 负向变异 3：seed 不匹配（不同 seed 比较，如 seed 42 vs seed 100），必须 Fail-Closed 触发 UNEXPLAINED_SEED_MISMATCH；
5. 负向变异 4：注入无词库依据的外来未知文本，必须 Fail-Closed 触发 UNEXPLAINED_BASELESS_SOURCE_ATOM；
6. 负向变异 5：词库内合法替换但没有变更依据（未在 selections 中采出且无消解决策），必须 Fail-Closed 触发 UNEXPLAINED_BASELESS_TAG_MUTATION；
7. 负向变异 6：在已有内容差异的真实样本中交换两标签，必须 Fail-Closed 触发 UNEXPLAINED_TAG_ORDER_VIOLATION；
8. 负向变异 7：在已有内容差异的真实样本中复制/增加标签频次，必须 Fail-Closed 触发 UNEXPLAINED_TAG_MULTIPLICITY_CHANGE；
9. 负向变异 8：源原子无消解决策静默消失，必须 Fail-Closed 触发 UNEXPLAINED_SILENT_ATOM_DROP；
10. 负向变异 9：第二轮 description 出现漂移，确定性门禁必须 Fail-Closed；
11. 负向变异 10：第二轮 diagnostic audit JSON 漂移，确定性门禁必须 Fail-Closed；
12. 负向变异 11：二次消解引发全字段语义状态、来源或顺序突变，幂等性门禁必须 Fail-Closed。
"""
from __future__ import annotations

import collections
import copy
import gzip
import json
from pathlib import Path
import sys
import unittest

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"

import nodes
from scratch.run_m5_3_audit import (
    load_authoritative_catalog_lookup,
    attribute_seed_difference,
    compare_atom_full_states,
    serialize_atom_full_state,
    stream_archive_records,
)


class TestM53AuditEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog_lookup = load_authoritative_catalog_lookup(DATA_DIR)
        cls.rules_data = json.loads((DATA_DIR / "conflict_rules.json").read_text(encoding="utf-8"))
        cls.affinity_data = json.loads((DATA_DIR / "context_affinity.json").read_text(encoding="utf-8"))

        fixture_path = REPO_DIR / "scratch" / "audit_m5_3_sample_seeds.json"
        if not fixture_path.exists():
            raise FileNotFoundError(
                f"Small sample fixture not found: {fixture_path}. "
                "Daily tests must read small sample and must not generate batches automatically."
            )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        records_map = {int(k): v for k, v in fixture["records"].items()}

        cls.record_0 = records_map[0]
        cls.record_2 = records_map[2]
        cls.record_42 = records_map[42]
        cls.base_item = cls.record_42["baseline"]
        cls.cur_item = cls.record_42

        prop_fixture_path = REPO_DIR / "scratch" / "audit_m5_3_prop_seeds.json"
        if prop_fixture_path.exists():
            cls.prop_records = json.loads(prop_fixture_path.read_text(encoding="utf-8"))
        else:
            cls.prop_records = {}

    def test_01_positive_real_cross_version_attribution_seed_42(self):
        """正向测试 1：相同输入与相同 seed（seed=42）跨版本真实对比，必须通过严谨因果证明。"""
        res = attribute_seed_difference(
            42,
            self.base_item,
            self.cur_item,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertTrue(res["is_explained"], f"Seed 42 real cross-version attribution failed: {res['unexplained_reasons']}")
        self.assertEqual(res["unexplained_reasons"], [])
        self.assertNotEqual(res["category"], "UNEXPLAINED")

    def test_01b_positive_real_cross_version_attribution_seed_0(self):
        """正向测试 2：相同输入与相同 seed（seed=0）跨版本真实对比，必须通过严谨因果证明。"""
        res = attribute_seed_difference(
            0,
            self.record_0["baseline"],
            self.record_0,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertTrue(res["is_explained"], f"Seed 0 real cross-version attribution failed: {res['unexplained_reasons']}")
        self.assertEqual(res["unexplained_reasons"], [])
        self.assertNotEqual(res["category"], "UNEXPLAINED")

    def test_01c_positive_real_cross_version_attribution_seed_2(self):
        """正向测试 3：相同输入与相同 seed（seed=2）跨版本真实对比，必须通过严谨因果证明。"""
        res = attribute_seed_difference(
            2,
            self.record_2["baseline"],
            self.record_2,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertTrue(res["is_explained"], f"Seed 2 real cross-version attribution failed: {res['unexplained_reasons']}")
        self.assertEqual(res["unexplained_reasons"], [])
        self.assertNotEqual(res["category"], "UNEXPLAINED")

    def test_02_negative_empty_rules_data_rejected(self):
        """负向测试 1：rules_data 替换为空字典时，因缺少消解规则定义，必须判定为 UNEXPLAINED。"""
        res = attribute_seed_difference(
            2,
            self.record_2["baseline"],
            self.record_2,
            self.catalog_lookup,
            rules_data={},  # 故意清空规则数据
            affinity_data=self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Empty rules_data MUST NOT be accepted as causal proof!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_MISSING_RULE_EVIDENCE", errors)

    def test_03_negative_empty_affinity_data_rejected(self):
        """负向测试 2：affinity_data 替换为空字典、无关假矩阵或单元全 null 时，必须判定为 UNEXPLAINED_NO_AFFINITY_EVIDENCE。"""
        # 1. 传入空字典
        res_empty = attribute_seed_difference(
            42,
            self.base_item,
            self.cur_item,
            self.catalog_lookup,
            self.rules_data,
            affinity_data={},
        )
        self.assertFalse(res_empty["is_explained"], "Empty affinity_data MUST NOT be accepted as causal proof!")
        self.assertEqual(res_empty["category"], "UNEXPLAINED")
        errors_empty = " ".join(res_empty["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors_empty)

        # 2. 传入无关假矩阵 {"matrix": {"unrelated": {}}} 验证 seed 42
        dummy_aff = {"matrix": {"unrelated": {}}}
        res_dummy_42 = attribute_seed_difference(
            42,
            self.base_item,
            self.cur_item,
            self.catalog_lookup,
            self.rules_data,
            affinity_data=dummy_aff,
        )
        self.assertFalse(res_dummy_42["is_explained"], "Unrelated dummy matrix on seed 42 MUST fail causal audit!")
        self.assertEqual(res_dummy_42["category"], "UNEXPLAINED")
        errors_dummy_42 = " ".join(res_dummy_42["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors_dummy_42)

        # 3. 传入无关假矩阵 {"matrix": {"unrelated": {}}} 验证 seed 2
        res_dummy_2 = attribute_seed_difference(
            2,
            self.record_2["baseline"],
            self.record_2,
            self.catalog_lookup,
            self.rules_data,
            affinity_data=dummy_aff,
        )
        self.assertFalse(res_dummy_2["is_explained"], "Unrelated dummy matrix on seed 2 MUST fail causal audit!")
        self.assertEqual(res_dummy_2["category"], "UNEXPLAINED")
        errors_dummy_2 = " ".join(res_dummy_2["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors_dummy_2)

        # 4. 传入单元格全为 null 的破坏矩阵（审查员反例），必须 Fail-Closed 触发 UNEXPLAINED_NO_AFFINITY_EVIDENCE
        null_cells_matrix = {
            "matrix": {
                ctx: {slot: None for slot in row}
                for ctx, row in self.affinity_data["matrix"].items()
            }
        }
        res_null_42 = attribute_seed_difference(
            42,
            self.base_item,
            self.cur_item,
            self.catalog_lookup,
            self.rules_data,
            affinity_data=null_cells_matrix,
        )
        self.assertFalse(res_null_42["is_explained"], "All-null matrix on seed 42 MUST fail causal audit!")
        self.assertEqual(res_null_42["category"], "UNEXPLAINED")
        errors_null_42 = " ".join(res_null_42["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors_null_42)

        res_null_2 = attribute_seed_difference(
            2,
            self.record_2["baseline"],
            self.record_2,
            self.catalog_lookup,
            self.rules_data,
            affinity_data=null_cells_matrix,
        )
        self.assertFalse(res_null_2["is_explained"], "All-null matrix on seed 2 MUST fail causal audit!")
        self.assertEqual(res_null_2["category"], "UNEXPLAINED")
        errors_null_2 = " ".join(res_null_2["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors_null_2)

        # 5. 传入单元格全为 neutral 的矩阵（审查员最新反例）：
        # 即使单元格合法非空、全为 neutral，但由于候选分布漂移且抽样重放失败，必须 Fail-Closed 触发 UNEXPLAINED_NO_AFFINITY_EVIDENCE
        neutral_cells_matrix = {
            "matrix": {
                ctx: {
                    slot: {"mode": "neutral"}
                    for slot in row
                }
                for ctx, row in self.affinity_data["matrix"].items()
            }
        }
        res_neutral_42 = attribute_seed_difference(
            42,
            self.base_item,
            self.cur_item,
            self.catalog_lookup,
            self.rules_data,
            affinity_data=neutral_cells_matrix,
        )
        self.assertFalse(res_neutral_42["is_explained"], "All-neutral matrix on seed 42 MUST fail causal audit!")
        self.assertEqual(res_neutral_42["category"], "UNEXPLAINED")
        errors_neutral_42 = " ".join(res_neutral_42["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors_neutral_42)
        self.assertIn("Affinity distribution mismatch", errors_neutral_42)

        res_neutral_2 = attribute_seed_difference(
            2,
            self.record_2["baseline"],
            self.record_2,
            self.catalog_lookup,
            self.rules_data,
            affinity_data=neutral_cells_matrix,
        )
        self.assertFalse(res_neutral_2["is_explained"], "All-neutral matrix on seed 2 MUST fail causal audit!")
        self.assertEqual(res_neutral_2["category"], "UNEXPLAINED")
        errors_neutral_2 = " ".join(res_neutral_2["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors_neutral_2)

    def test_03b_negative_missing_sampling_rationale_rejected(self):
        """负向测试 2b：缺失 sampling_rationale 时，严禁放行，必须判定为 UNEXPLAINED。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        da = json.loads(tampered_cur["round_a"]["diagnostics_audit"]) if isinstance(tampered_cur["round_a"]["diagnostics_audit"], str) else tampered_cur["round_a"]["diagnostics_audit"]
        for s in da["selections"]:
            if s.get("selector") == "clothing":
                s["sampling_rationale"] = None
        tampered_cur["round_a"]["diagnostics_audit"] = json.dumps(da)

        res = attribute_seed_difference(
            42, self.base_item, tampered_cur, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res["is_explained"], "Missing sampling_rationale MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertTrue(
            "UNEXPLAINED_MISSING_SAMPLING_RATIONALE" in errors or "Missing sampling_rationale" in errors or "UNEXPLAINED_NO_AFFINITY_EVIDENCE" in errors or "UNEXPLAINED_BASELESS_TAG_MUTATION" in errors or "lacks sampling rationale" in errors,
            f"Expected missing sampling rationale error, got: {errors}"
        )

    def test_03c_negative_empty_distribution_rejected(self):
        """负向测试 2c：sampling_rationale 中 distribution 为空字典时，必须判定为 UNEXPLAINED。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        da = json.loads(tampered_cur["round_a"]["diagnostics_audit"]) if isinstance(tampered_cur["round_a"]["diagnostics_audit"], str) else tampered_cur["round_a"]["diagnostics_audit"]
        for s in da["selections"]:
            if s.get("selector") == "clothing" and s.get("sampling_rationale"):
                s["sampling_rationale"]["distribution"] = {}
        tampered_cur["round_a"]["diagnostics_audit"] = json.dumps(da)

        res = attribute_seed_difference(
            42, self.base_item, tampered_cur, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res["is_explained"], "Empty distribution MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertTrue(
            "Missing or empty distribution" in errors or "UNEXPLAINED_NO_AFFINITY_EVIDENCE" in errors,
            f"Expected empty distribution error, got: {errors}"
        )

    def test_03d_negative_sampling_replay_mismatch_rejected(self):
        """负向测试 2d：完整证据下，确定性抽样重放结果与目标选项不匹配时，必须判定为 UNEXPLAINED。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        da = json.loads(tampered_cur["round_a"]["diagnostics_audit"]) if isinstance(tampered_cur["round_a"]["diagnostics_audit"], str) else tampered_cur["round_a"]["diagnostics_audit"]
        for s in da["selections"]:
            if s.get("selector") == "clothing" and s.get("sampling_rationale"):
                s["sampling_rationale"]["final_selection"]["selected_id"] = "completely_mismatched_sampled_candidate"
        tampered_cur["round_a"]["diagnostics_audit"] = json.dumps(da)

        res = attribute_seed_difference(
            42, self.base_item, tampered_cur, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res["is_explained"], "Sampling replay mismatch MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertTrue(
            "Controlled sampling replay mismatch" in errors or "UNEXPLAINED_NO_AFFINITY_EVIDENCE" in errors,
            f"Expected replay mismatch error, got: {errors}"
        )

    def test_03e_negative_distribution_drift_rejected(self):
        """负向测试 2e：完整证据下，矩阵权重微调导致分布漂移 (> 1e-4) 时，必须判定为 UNEXPLAINED。"""
        tampered_aff = copy.deepcopy(self.affinity_data)
        # 微调 Seed 42 核心活跃情境 (domestic) 下的候选权重导致计算分布发生漂移
        tampered_aff["matrix"]["domestic"]["clothing"]["candidates"][0]["weight"] += 5.0

        res = attribute_seed_difference(
            42, self.base_item, self.cur_item, self.catalog_lookup, self.rules_data, tampered_aff
        )
        self.assertFalse(res["is_explained"], "Distribution drift (> 1e-4) MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("Affinity distribution mismatch", errors)

    def test_03f_negative_removed_atom_missing_evidence_rejected(self):
        """负向测试 2f：删除侧原子缺失消解决策依据且缺失目标侧抽样记录时，必须准确判定为 UNEXPLAINED。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        da = json.loads(tampered_cur["round_a"]["diagnostics_audit"]) if isinstance(tampered_cur["round_a"]["diagnostics_audit"], str) else tampered_cur["round_a"]["diagnostics_audit"]
        # 移除 character 槽位在目标侧的选择证据，导致 baseline 的 neighbor_girlfriend 缺失依据
        da["selections"] = [s for s in da["selections"] if s.get("selector") not in ("character", "character_state")]
        tampered_cur["round_a"]["diagnostics_audit"] = json.dumps(da)

        res = attribute_seed_difference(
            42, self.base_item, tampered_cur, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res["is_explained"], "Removed atom without evidence MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_MISSING_EVIDENCE_FOR_REMOVED_ATOM", errors)
        self.assertIn("character", errors)

    def test_03g_negative_auto_sampling_missing_rationale_rejected(self):
        """负向测试 2g：自动亲和抽样（如 lighting 槽位 mode='auto'）缺失 sampling_rationale 时，严禁豁免，必须判定为 UNEXPLAINED。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        da = json.loads(tampered_cur["round_a"]["diagnostics_audit"]) if isinstance(tampered_cur["round_a"]["diagnostics_audit"], str) else tampered_cur["round_a"]["diagnostics_audit"]
        for s in da["selections"]:
            if s.get("selector") == "lighting":
                s["sampling_rationale"] = None
        tampered_cur["round_a"]["diagnostics_audit"] = json.dumps(da)

        res = attribute_seed_difference(
            42, self.base_item, tampered_cur, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res["is_explained"], "Auto sampling lacking sampling_rationale MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_MISSING_SAMPLING_RATIONALE", errors)
        self.assertIn("lighting", errors)

    def test_03h_negative_auto_sampling_neutral_matrix_rejected(self):
        """负向测试 2h：自动亲和抽样（如 lighting 槽位 mode='auto'）在全 neutral 假矩阵下，必须因分布漂移拦截，严禁豁免！"""
        neutral_aff = copy.deepcopy(self.affinity_data)
        for ctx, row in neutral_aff["matrix"].items():
            if "lighting" in row:
                row["lighting"] = {"mode": "neutral"}

        res = attribute_seed_difference(
            42, self.base_item, self.cur_item, self.catalog_lookup, self.rules_data, neutral_aff
        )
        self.assertFalse(res["is_explained"], "All-neutral matrix on auto slot lighting MUST fail causal audit!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_NO_AFFINITY_EVIDENCE", errors)
        self.assertIn("Affinity distribution mismatch for slot 'lighting'", errors)

    def test_03i_negative_removed_atom_baseline_evidence_missing_rejected(self):
        """负向测试 2i：删除侧原子在 baseline 记录中缺失源原子与规则证据时，必须判定为 UNEXPLAINED_BASELESS_SOURCE_ATOM。"""
        tampered_base = copy.deepcopy(self.base_item)
        # 将 baseline 的 source_atoms 中 'bedside lamp' 移除，模拟基线侧证据缺失
        tampered_base["source_atoms"] = [a for a in tampered_base["source_atoms"] if a.get("text") != "bedside lamp"]

        res = attribute_seed_difference(
            42, tampered_base, self.cur_item, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res["is_explained"], "Removed atom lacking baseline source evidence MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_BASELESS_SOURCE_ATOM", errors)
        self.assertIn("bedside lamp", errors)

    def test_03j_positive_legitimate_rule_product_removal_recognized(self):
        """规则产物验证 1：合法规则生成产物及其触发源变更依据能被正确识别（决策、输出与触发条件完全一致）。"""
        target_text = "ink embedded in dermis"
        tampered_cur = copy.deepcopy(self.cur_item)
        cur_rnd = tampered_cur["round_a"]

        # 模拟目标侧未触发纹身（例如纹身槽位选择为无/固定选择），无 inject 决策，输出无融合词，母原子合理消失
        # 1. 移除目标侧所有纹身原子及融合产物
        cur_rnd["final_atoms"] = [a for a in cur_rnd["final_atoms"] if a.get("source_slot") != "tattoo"]
        for idx, a in enumerate(cur_rnd["final_atoms"]):
            a["tag_order"] = idx
        cur_rnd["post_secondary_atoms"] = copy.deepcopy(cur_rnd["final_atoms"])

        # 2. 移除目标侧 positive 文本中的纹身与融合标签
        removed_texts = {
            "正 kanji tally marks tattoo on inner thigh",
            "handwritten scratched tally lines",
            target_text,
        }
        tags = [t.strip() for t in cur_rnd["positive"].split(",") if t.strip() not in removed_texts]
        cur_rnd["positive"] = ", ".join(tags)

        # 3. 移除目标侧 source_atoms 中的纹身
        cur_rnd["source_atoms"] = [a for a in cur_rnd.get("source_atoms", []) if a.get("source_slot") != "tattoo"]

        # 4. 移除目标侧 tattoo_dermal_fusion inject 决策
        cur_rnd["decisions"] = [d for d in cur_rnd["decisions"] if d.get("rule_id") != "tattoo_dermal_fusion"]

        # 5. 更新目标侧 diagnostics_audit selections：将 tattoo 替换为固定模式选择 "无 (None)"
        audit_doc = json.loads(cur_rnd["diagnostics_audit"]) if isinstance(cur_rnd["diagnostics_audit"], str) else copy.deepcopy(cur_rnd["diagnostics_audit"])
        new_selections = [
            sel for sel in audit_doc.get("selections", [])
            if sel.get("selector") != "tattoo" and sel.get("slot") != "tattoo"
        ]
        new_selections.append({
            "selector": "tattoo",
            "slot": "tattoo",
            "selected_id": "无 (None)",
            "mode": "fixed",
            "raw_value": "无 (None)",
            "sampling_rationale": None,
        })
        audit_doc["selections"] = new_selections
        cur_rnd["diagnostics_audit"] = json.dumps(audit_doc)

        res = attribute_seed_difference(
            42, self.base_item, tampered_cur, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertTrue(res["is_explained"], f"Legitimate rule product removal MUST be explained, got errors: {res['unexplained_reasons']}")
        self.assertEqual(res["unexplained_reasons"], [])
        attributions = [a for a in res["attributions"] if a.get("text") == target_text]
        self.assertTrue(len(attributions) > 0, "Expected attribution for rule product")
        self.assertEqual(attributions[0]["type"], "conflict_rule_resolved")
        self.assertEqual(attributions[0]["rule_id"], "tattoo_dermal_fusion")

    def test_03k_negative_forged_rule_product_evidence_rejected(self):
        """规则产物验证 2：目标侧已生成产物静默丢失、借用真实规则名伪造产物文本、或伪造未知规则依据均必须被拒绝。"""
        # 1. 目标侧记录已生成产物，但在无后续合法删除决策的情况下静默丢失（删目标产物保留生成决策）
        tampered_cur_a = copy.deepcopy(self.cur_item)
        cur_rnd_a = tampered_cur_a["round_a"]
        target_text = "ink embedded in dermis"
        cur_rnd_a["final_atoms"] = [a for a in cur_rnd_a["final_atoms"] if a.get("text") != target_text]
        for idx, a in enumerate(cur_rnd_a["final_atoms"]):
            a["tag_order"] = idx
        cur_rnd_a["post_secondary_atoms"] = copy.deepcopy(cur_rnd_a["final_atoms"])
        tags = [t.strip() for t in cur_rnd_a["positive"].split(",") if t.strip() != target_text]
        cur_rnd_a["positive"] = ", ".join(tags)

        res_a = attribute_seed_difference(
            42, self.base_item, tampered_cur_a, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res_a["is_explained"], "Silently dropped target rule product MUST be rejected!")
        self.assertEqual(res_a["category"], "UNEXPLAINED")
        errors_a = " ".join(res_a["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_SILENT_ATOM_DROP", errors_a)
        self.assertIn(target_text, errors_a)

        # 2. 借用真实规则名（tattoo_dermal_fusion）但产物文本为未在规则中定义的伪造内容
        fake_atom = {
            "atom_id": "atom_forged_rule_product_999",
            "text": "forged dermal fusion text",
            "source_item_id": "tattoo_dermal_fusion_fusion",
            "source_slot": "tattoo",
            "parent_ids": ["atom_aa32cdedcef3a0d0af71a9a6"],
            "span_order": 0,
            "tag_order": 99,
        }
        tampered_base_b = copy.deepcopy(self.base_item)
        tampered_base_b["final_atoms"].append(fake_atom)
        tampered_base_b["positive"] += ", forged dermal fusion text"
        tampered_base_b["decisions"].append({
            "action": "inject",
            "rule_id": "tattoo_dermal_fusion",
            "produced_atom_ids": ["atom_forged_rule_product_999"],
            "after_text": "forged dermal fusion text",
            "decision_id": "dec_fake_real_rule_999",
            "winner_atom_ids": ["atom_aa32cdedcef3a0d0af71a9a6"],
        })
        res_b = attribute_seed_difference(
            42, tampered_base_b, self.cur_item, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res_b["is_explained"], "Rule product with real rule name but forged text MUST be rejected!")
        self.assertEqual(res_b["category"], "UNEXPLAINED")
        errors_b = " ".join(res_b["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_BASELESS_SOURCE_ATOM", errors_b)
        self.assertIn("forged dermal fusion text", errors_b)

        # 3. 伪造未知规则依据（decision 中的 rule_id 不在 rules_data 中）
        tampered_base_c = copy.deepcopy(self.base_item)
        tampered_base_c["final_atoms"].append(fake_atom)
        tampered_base_c["positive"] += ", forged dermal fusion text"
        tampered_base_c["decisions"].append({
            "action": "inject",
            "rule_id": "fake_alien_mutation_rule_xyz",
            "produced_atom_ids": ["atom_forged_rule_product_999"],
            "after_text": "forged dermal fusion text",
            "decision_id": "dec_fake_999",
            "winner_atom_ids": ["atom_aa32cdedcef3a0d0af71a9a6"],
        })
        res_c = attribute_seed_difference(
            42, tampered_base_c, self.cur_item, self.catalog_lookup, self.rules_data, self.affinity_data
        )
        self.assertFalse(res_c["is_explained"], "Rule product with fake rule_id MUST be rejected!")
        self.assertEqual(res_c["category"], "UNEXPLAINED")
        errors_c = " ".join(res_c["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_MISSING_RULE_EVIDENCE", errors_c)
        self.assertIn("fake_alien_mutation_rule_xyz", errors_c)

    def test_04_negative_seed_mismatch_rejected(self):
        """负向测试 3：seed 不匹配（如将 seed 0 误当成 seed 42 的目标输出），必须判定为 UNEXPLAINED_SEED_MISMATCH。"""
        res = attribute_seed_difference(
            42,
            self.base_item,       # seed 42
            self.record_0,        # seed 0
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Mismatched seeds MUST NOT be explained!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_SEED_MISMATCH", errors)

    def test_05_negative_baseless_text_injection(self):
        """负向测试 4：注入无词库依据的外来文本，必须判定为 UNEXPLAINED_BASELESS_SOURCE_ATOM。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        fake_atom = {
            "atom_id": "fake_c_99",
            "text": "completely_unauthorized_alien_suit_text_xyz",
            "source_slot": "clothing",
            "source_item_id": "alien_suit",
            "tag_order": 99,
            "span_order": 0,
        }
        tampered_cur["round_a"]["source_atoms"].append(fake_atom)
        tampered_cur["round_a"]["final_atoms"].append(fake_atom)
        tampered_cur["round_a"]["positive"] += ", completely_unauthorized_alien_suit_text_xyz"

        res = attribute_seed_difference(
            42,
            self.base_item,
            tampered_cur,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Baseless text injection MUST NOT be explained!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertTrue(
            "UNEXPLAINED_BASELESS_SOURCE_ATOM" in errors or "UNEXPLAINED_UNKNOWN_CATALOG_ITEM" in errors,
            f"Expected baseless source atom, got: {errors}",
        )

    def test_06_negative_catalog_valid_replacement_without_evidence(self):
        """负向测试 5：词库内合法替换但没有变更依据（篡改为有效词库词汇，但在 selections 和决策中无记录），必须判定为 UNEXPLAINED_BASELESS_TAG_MUTATION。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        # 将一个终态原子篡改为另一个词库中合法存在但本次未采出的 item（例如 flowing combat battle robe，属于 battle_robe）
        found = False
        for a in tampered_cur["round_a"]["final_atoms"]:
            if a["source_slot"] == "clothing":
                a["text"] = "flowing combat battle robe"
                a["source_item_id"] = "battle_robe"
                found = True
                break
        self.assertTrue(found, "Test requires at least one clothing atom to tamper")

        # 同步更新 positive 中的文字
        from lib.lexer import parse_prompt
        tags = [t.text.strip() for t in parse_prompt(tampered_cur["round_a"]["positive"]).tags]
        for i, t in enumerate(tags):
            if "dress" in t or "suit" in t or "skirt" in t or "robe" in t or "clothes" in t:
                tags[i] = "flowing combat battle robe"
                break
        tampered_cur["round_a"]["positive"] = ", ".join(tags)

        res = attribute_seed_difference(
            42,
            self.base_item,
            tampered_cur,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Catalog-valid tag mutation without selection evidence MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_BASELESS_TAG_MUTATION", errors)

    def test_07_negative_tag_swap_on_divergent_sample(self):
        """负向测试 6：在已有内容差异的真实跨版本样本中交换两标签及对应原子，必须被判定为 UNEXPLAINED_TAG_ORDER_VIOLATION。"""
        # 1. 验证在 seed 42 已有内容差异的样本中交换两共有标签
        tampered_cur = copy.deepcopy(self.cur_item)
        from lib.lexer import parse_prompt
        cur_tags = [t.text.strip() for t in parse_prompt(tampered_cur["round_a"]["positive"]).tags]
        base_tags = [t.text.strip() for t in parse_prompt(self.base_item["positive"]).tags]
        shared = [t for t in cur_tags if t in set(base_tags)]
        self.assertGreaterEqual(len(shared), 2, "Test requires at least 2 shared tags")
        t0, t1 = shared[0], shared[1]

        # 同步交换 positive 中的文本顺序
        idx0, idx1 = cur_tags.index(t0), cur_tags.index(t1)
        cur_tags[idx0], cur_tags[idx1] = cur_tags[idx1], cur_tags[idx0]
        tampered_cur["round_a"]["positive"] = ", ".join(cur_tags)

        # 同步交换 final_atoms 中对应的原子和其 tag_order，确保装配内生一致性通过 Step 2
        a_idx0 = next(i for i, a in enumerate(tampered_cur["round_a"]["final_atoms"]) if a.get("text") == t0)
        a_idx1 = next(i for i, a in enumerate(tampered_cur["round_a"]["final_atoms"]) if a.get("text") == t1)
        tampered_cur["round_a"]["final_atoms"][a_idx0], tampered_cur["round_a"]["final_atoms"][a_idx1] = (
            tampered_cur["round_a"]["final_atoms"][a_idx1],
            tampered_cur["round_a"]["final_atoms"][a_idx0],
        )
        tampered_cur["round_a"]["final_atoms"][a_idx0]["tag_order"], tampered_cur["round_a"]["final_atoms"][a_idx1]["tag_order"] = (
            tampered_cur["round_a"]["final_atoms"][a_idx1]["tag_order"],
            tampered_cur["round_a"]["final_atoms"][a_idx0]["tag_order"],
        )

        res = attribute_seed_difference(
            42,
            self.base_item,
            tampered_cur,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Tag swap on divergent sample MUST NOT be explained!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_TAG_ORDER_VIOLATION", errors)

        # 2. 验证审查员反例：将 seed 0 的一个共有 props 标签从第 14 位移到首位，严禁被 migrating_slots 盲目豁免
        tampered_0 = copy.deepcopy(self.record_0)
        cur_tags_0 = [t.text.strip() for t in parse_prompt(tampered_0["round_a"]["positive"]).tags]
        props_idx = None
        for idx, a in enumerate(tampered_0["round_a"]["final_atoms"]):
            if a.get("source_slot") == "props":
                props_idx = idx
                break
        self.assertIsNotNone(props_idx, "seed 0 must have at least one props atom to tamper")
        props_atom = tampered_0["round_a"]["final_atoms"].pop(props_idx)
        tampered_0["round_a"]["final_atoms"].insert(0, props_atom)
        for idx, a in enumerate(tampered_0["round_a"]["final_atoms"]):
            a["tag_order"] = idx
        tampered_0["round_a"]["post_secondary_atoms"] = copy.deepcopy(tampered_0["round_a"]["final_atoms"])
        p_tag = cur_tags_0.pop(props_idx)
        cur_tags_0.insert(0, p_tag)
        tampered_0["round_a"]["positive"] = ", ".join(cur_tags_0)

        res_props_swap = attribute_seed_difference(
            0,
            self.record_0["baseline"],
            tampered_0,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res_props_swap["is_explained"], "Moving props to position 0 MUST NOT be explained!")
        self.assertEqual(res_props_swap["category"], "UNEXPLAINED")
        errors_props = " ".join(res_props_swap["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_TAG_ORDER_VIOLATION", errors_props)

        # 3. 验证审查员最新反例：在 seed 0 中交换两个同槽位 makeup 共有标签（'grab redness on face' 与 'transferred lipstick on lips'），
        # 并同步更新文本、终态原子和二次快照，必须 Fail-Closed 被严格拦截，杜绝同槽位整体豁免
        tampered_makeup = copy.deepcopy(self.record_0)
        t_mk1 = "grab redness on face"
        t_mk2 = "transferred lipstick on lips"
        c_rnd = tampered_makeup["round_a"]
        pos_str = c_rnd["positive"]
        c_rnd["positive"] = pos_str.replace(t_mk1, "___PL_MK___").replace(t_mk2, t_mk1).replace("___PL_MK___", t_mk2)
        for atom_list in (c_rnd["final_atoms"], c_rnd["post_secondary_atoms"]):
            m_idx1 = next(i for i, a in enumerate(atom_list) if a.get("text") == t_mk1)
            m_idx2 = next(i for i, a in enumerate(atom_list) if a.get("text") == t_mk2)
            atom_list[m_idx1]["tag_order"], atom_list[m_idx2]["tag_order"] = (
                atom_list[m_idx2]["tag_order"],
                atom_list[m_idx1]["tag_order"],
            )
            atom_list[m_idx1], atom_list[m_idx2] = atom_list[m_idx2], atom_list[m_idx1]

        res_makeup_swap = attribute_seed_difference(
            0,
            self.record_0["baseline"],
            tampered_makeup,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res_makeup_swap["is_explained"], "Same-slot makeup tag swap on seed 0 MUST NOT be explained!")
        self.assertEqual(res_makeup_swap["category"], "UNEXPLAINED")
        errors_mk = " ".join(res_makeup_swap["unexplained_reasons"])
        self.assertTrue(
            "UNEXPLAINED_ASSEMBLY_ORDER_VIOLATION" in errors_mk or "UNEXPLAINED_TAG_ORDER_VIOLATION" in errors_mk,
            f"Expected assembly or tag order violation, got: {errors_mk}"
        )

    def test_08_negative_tag_multiplicity_mutation_on_divergent_sample(self):
        """负向测试 7：在已有内容差异的真实跨版本样本中增加/复制标签频次及对应原子，必须判定为 UNEXPLAINED_TAG_MULTIPLICITY_CHANGE。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        from lib.lexer import parse_prompt
        cur_tags = [t.text.strip() for t in parse_prompt(tampered_cur["round_a"]["positive"]).tags]
        base_tags = [t.text.strip() for t in parse_prompt(self.base_item["positive"]).tags]
        shared = [t for t in cur_tags if t in set(base_tags)]
        self.assertGreaterEqual(len(shared), 1, "Test requires at least 1 shared tag")
        dup_tag = shared[0]

        # 同步复制 positive 中的文本
        cur_tags.insert(2, dup_tag)
        tampered_cur["round_a"]["positive"] = ", ".join(cur_tags)

        # 同步在 final_atoms 中插入具有相同 text 的原子
        matching_atom = next(a for a in tampered_cur["round_a"]["final_atoms"] if a.get("text") == dup_tag)
        dup_atom = copy.deepcopy(matching_atom)
        dup_atom["atom_id"] = "atom_dup_test_123"
        dup_atom["tag_order"] = 2
        tampered_cur["round_a"]["final_atoms"].insert(2, dup_atom)

        res = attribute_seed_difference(
            42,
            self.base_item,
            tampered_cur,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Tag duplication without source atom MUST NOT be explained!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_TAG_MULTIPLICITY_CHANGE", errors)

    def test_09_negative_silent_atom_drop(self):
        """负向测试 8：源原子被悄悄从终态中丢弃而没有 recorded decision，必须被判定为 UNEXPLAINED_SILENT_ATOM_DROP。"""
        tampered_cur = copy.deepcopy(self.cur_item)
        dropped_atom = tampered_cur["round_a"]["final_atoms"].pop(0)
        from lib.lexer import parse_prompt
        tags = [t.text.strip() for t in parse_prompt(tampered_cur["round_a"]["positive"]).tags]
        tags = [t for t in tags if t != dropped_atom["text"]]
        tampered_cur["round_a"]["positive"] = ", ".join(tags)

        res = attribute_seed_difference(
            42,
            self.base_item,
            tampered_cur,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Silent atom drop MUST NOT be explained!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_SILENT_ATOM_DROP", errors)

    def test_10_negative_round2_description_determinism(self):
        """负向测试 9：第二轮 description 出现漂移时，确定性断言逻辑必须判定为 False。"""
        r1_pos = "test prompt"
        r1_neg = "low quality"
        r1_desc = "description 1"
        d_audit1 = {"schema_version": "2.0"}

        r2_pos = "test prompt"
        r2_neg = "low quality"
        r2_desc = "description 2 [DRIFTED]"
        d_audit2 = {"schema_version": "2.0"}

        det_pass = (
            r1_pos == r2_pos and
            r1_neg == r2_neg and
            r1_desc == r2_desc and
            d_audit1 == d_audit2
        )
        self.assertFalse(det_pass, "Description drift between rounds MUST fail determinism!")

    def test_11_negative_round2_audit_json_determinism(self):
        """负向测试 10：第二轮 diagnostic audit JSON 漂移时，确定性断言逻辑必须判定为 False。"""
        r1_pos = "test prompt"
        r1_neg = "low quality"
        r1_desc = "description 1"
        d_audit1 = {"schema_version": "2.0", "seed": 42}

        r2_pos = "test prompt"
        r2_neg = "low quality"
        r2_desc = "description 1"
        d_audit2 = {"schema_version": "2.0", "seed": 42, "drifted_field": True}

        det_pass = (
            r1_pos == r2_pos and
            r1_neg == r2_neg and
            r1_desc == r2_desc and
            d_audit1 == d_audit2
        )
        self.assertFalse(det_pass, "Audit JSON drift between rounds MUST fail determinism!")

    def test_12_negative_full_field_idempotency_mutation(self):
        """负向测试 11：二次消解引发全字段语义状态（facts/provenance/origin 等）突变或快照缺失时，门禁必须 Fail-Closed。"""
        from lib.models import PromptAtom, SelectionOrigin, TagProvenance, SpanType, SemanticFacts, is_external_atom

        # 1. 验证真实 PromptAtom 对 origin.mode 变异的灵敏拦截（审查员反例）
        atom_base = PromptAtom(
            text="blue business suit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="suit_1",
            origin=SelectionOrigin(mode="random", selector="clothing"),
            provenance=TagProvenance(item_id="suit_1", rule_id=None, source_mode=None),
        )
        atom_mod_origin = PromptAtom(
            text="blue business suit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="suit_1",
            origin=SelectionOrigin(mode="override", selector="clothing"),
            provenance=TagProvenance(item_id="suit_1", rule_id=None, source_mode=None),
        )
        # 该修改导致 is_external_atom() 从 False 变为 True
        self.assertFalse(is_external_atom(atom_base))
        self.assertTrue(is_external_atom(atom_mod_origin))
        ok_orig, diffs_orig = compare_atom_full_states([atom_base], [atom_mod_origin])
        self.assertFalse(ok_orig, "compare_atom_full_states MUST fail when origin.mode is mutated!")
        self.assertTrue(any("origin" in d for d in diffs_orig), f"Expected origin diff, got: {diffs_orig}")

        # 2. 验证真实 PromptAtom 对 provenance.source_mode 变异的灵敏拦截（审查员反例）
        atom_mod_src_mode = PromptAtom(
            text="blue business suit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="suit_1",
            origin=SelectionOrigin(mode="random", selector="clothing"),
            provenance=TagProvenance(item_id="suit_1", rule_id=None, source_mode="override"),
        )
        self.assertTrue(is_external_atom(atom_mod_src_mode))
        ok_src_mode, diffs_src_mode = compare_atom_full_states([atom_base], [atom_mod_src_mode])
        self.assertFalse(ok_src_mode, "compare_atom_full_states MUST fail when provenance.source_mode is mutated!")
        self.assertTrue(any("provenance" in d for d in diffs_src_mode), f"Expected provenance diff, got: {diffs_src_mode}")

        # 3. 验证真实 PromptAtom 对 provenance.rule_id 变异的灵敏拦截（审查员反例）
        atom_mod_rule = PromptAtom(
            text="blue business suit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="suit_1",
            origin=SelectionOrigin(mode="random", selector="clothing"),
            provenance=TagProvenance(item_id="suit_1", rule_id="rule_injected_test", source_mode=None),
        )
        ok_rule, diffs_rule = compare_atom_full_states([atom_base], [atom_mod_rule])
        self.assertFalse(ok_rule, "compare_atom_full_states MUST fail when provenance.rule_id is mutated!")
        self.assertTrue(any("provenance_rule_id" in d or "rule_id" in d for d in diffs_rule), f"Expected rule_id diff, got: {diffs_rule}")

        # 4. 验证 compare_atom_full_states 对 facts 变异的灵敏拦截
        atom_dict_base = {
            "atom_id": "atom_test_1",
            "text": "test tag",
            "source_slot": "clothing",
            "source_item_id": "item_1",
            "tag_order": 0,
            "span_order": 0,
            "facts": {"cut": "long", "color": "blue"},
            "parent_ids": ["item_1"],
            "provenance_kind": "catalog",
            "provenance_item_id": "item_1",
        }
        atom_tampered_facts = copy.deepcopy(atom_dict_base)
        atom_tampered_facts["facts"] = {"cut": "short", "color": "blue"}

        ok_facts, diffs_facts = compare_atom_full_states([atom_dict_base], [atom_tampered_facts])
        self.assertFalse(ok_facts, "compare_atom_full_states MUST fail when facts are mutated!")
        self.assertTrue(any("facts" in d for d in diffs_facts), f"Expected facts diff, got: {diffs_facts}")

        # 5. 验证在 attribute_seed_difference 离线复核中对 post_secondary_atoms 变异的严格拦截
        tampered_cur = copy.deepcopy(self.cur_item)
        tampered_cur["round_a"]["post_secondary_atoms"] = copy.deepcopy(tampered_cur["round_a"]["final_atoms"])
        tampered_cur["round_a"]["post_secondary_atoms"][0]["facts"] = {"tampered_semantic_fact": True}

        res_facts = attribute_seed_difference(
            42,
            self.base_item,
            tampered_cur,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res_facts["is_explained"], "Post-secondary full atom mutation MUST be rejected!")
        self.assertEqual(res_facts["category"], "UNEXPLAINED")
        errors_facts = " ".join(res_facts["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_SECONDARY_IDEMPOTENCY_MUTATION", errors_facts)

        # 6. 验证缺失 post_secondary_atoms 快照时，必须 Fail-Closed 触发 UNEXPLAINED_MISSING_SECONDARY_SNAPSHOT（审查员反例）
        tampered_missing_snap = copy.deepcopy(self.cur_item)
        if "post_secondary_atoms" in tampered_missing_snap["round_a"]:
            del tampered_missing_snap["round_a"]["post_secondary_atoms"]

        res_missing = attribute_seed_difference(
            42,
            self.base_item,
            tampered_missing_snap,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res_missing["is_explained"], "Missing post_secondary_atoms snapshot MUST NOT be accepted!")
        self.assertEqual(res_missing["category"], "UNEXPLAINED")
        errors_missing = " ".join(res_missing["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_MISSING_SECONDARY_SNAPSHOT", errors_missing)

    def test_04a_positive_prop_tag_subsampling_seeds_124_149_200(self):
        """道具归因正向验证：Representative Seeds 124, 149, 200 道具标签受手部容量与独立子流 RNG 抽样合法排除，必须 100% 通过受控重放证明。"""
        self.assertTrue(len(self.prop_records) >= 3, "Prop fixture records 124, 149, 200 must be available")
        for s in (124, 149, 200):
            rec = self.prop_records[str(s)]
            res = attribute_seed_difference(
                s,
                rec["baseline"],
                rec,
                self.catalog_lookup,
                self.rules_data,
                self.affinity_data,
            )
            self.assertTrue(
                res["is_explained"],
                f"Seed {s} MUST be explained by deterministic replay, got: {res['unexplained_reasons']}"
            )
            self.assertEqual(res["unexplained_reasons"], [])
            prop_atts = [a for a in res["attributions"] if a.get("type") == "prop_tag_subsampling"]
            self.assertTrue(
                len(prop_atts) > 0,
                f"Seed {s} expected prop_tag_subsampling attribution, got: {res['attributions']}"
            )
            self.assertEqual(prop_atts[0]["slot"], "props")

    def test_04b_negative_prop_tampered_source_tag_fails_replay(self):
        """道具归因负向反例 1：伪造或篡改目标侧道具源原子文字，无法通过确定性受控重放，必须判定为 UNEXPLAINED_PROP_REPLAY_MISMATCH。"""
        rec_124 = self.prop_records.get("124")
        self.assertIsNotNone(rec_124, "Seed 124 fixture must be present")
        tampered = copy.deepcopy(rec_124)
        for a in tampered["round_a"]["source_atoms"]:
            if a.get("source_slot") == "props":
                a["text"] = "forged prop atom text"

        res = attribute_seed_difference(
            124,
            tampered["baseline"],
            tampered,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Tampered prop source atoms MUST NOT pass deterministic replay!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_PROP_REPLAY_MISMATCH", errors)

    def test_04c_negative_prop_silent_atom_drop_rejected(self):
        """道具归因负向反例 2：目标侧已生成道具原子在终态无规则依据静默丢失，必须判定为 UNEXPLAINED_SILENT_ATOM_DROP。"""
        rec_124 = self.prop_records.get("124")
        self.assertIsNotNone(rec_124, "Seed 124 fixture must be present")
        tampered = copy.deepcopy(rec_124)
        cur_atoms = tampered["round_a"]["final_atoms"]
        tampered["round_a"]["final_atoms"] = [a for a in cur_atoms if a.get("source_slot") != "props"]
        for idx, a in enumerate(tampered["round_a"]["final_atoms"]):
            a["tag_order"] = idx
        tampered["round_a"]["post_secondary_atoms"] = copy.deepcopy(tampered["round_a"]["final_atoms"])
        pos_tags = [t.strip() for t in tampered["round_a"]["positive"].split(",") if "screen light glowing on face" not in t]
        tampered["round_a"]["positive"] = ", ".join(pos_tags)

        res = attribute_seed_difference(
            124,
            tampered["baseline"],
            tampered,
            self.catalog_lookup,
            self.rules_data,
            self.affinity_data,
        )
        self.assertFalse(res["is_explained"], "Silently dropped prop atom MUST be rejected!")
        self.assertEqual(res["category"], "UNEXPLAINED")
        errors = " ".join(res["unexplained_reasons"])
        self.assertIn("UNEXPLAINED_SILENT_ATOM_DROP", errors)

    def test_04d_negative_truncated_archive_stream_rejected(self):
        """流式读取负向反例：截断归档（中途 EOF 或缺少 ] 闭合）必须严格抛出 RuntimeError，严禁假通过。"""
        import tempfile
        # 反例 A: JSON 解码过程中途遭遇 EOF 截断
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tf:
            tpath_a = Path(tf.name)
        try:
            with gzip.open(tpath_a, "wt", encoding="utf-8") as gf:
                gf.write('{"metadata": {"total_seeds": 10}, "records": [{"seed": 0}, {"seed": 1')
            with self.assertRaises(RuntimeError) as ctx_a:
                _, gen_a = stream_archive_records(tpath_a)
                for _ in gen_a:
                    pass
            self.assertIn("Truncated archive stream", str(ctx_a.exception))
        finally:
            tpath_a.unlink(missing_ok=True)

        # 反例 B: 记录完整但未遭遇闭合括号 ] 遇到 EOF 截断
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tf:
            tpath_b = Path(tf.name)
        try:
            with gzip.open(tpath_b, "wt", encoding="utf-8") as gf:
                gf.write('{"metadata": {"total_seeds": 10}, "records": [{"seed": 0}, {"seed": 1}')
            with self.assertRaises(RuntimeError) as ctx_b:
                _, gen_b = stream_archive_records(tpath_b)
                for _ in gen_b:
                    pass
            self.assertIn("Truncated archive stream", str(ctx_b.exception))
            self.assertIn("closing array ']'", str(ctx_b.exception))
        finally:
            tpath_b.unlink(missing_ok=True)

        # 反例 C: 数组正常闭合 ] 但缺失顶层闭合花括号 } 遇到 EOF 截断
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tf:
            tpath_c = Path(tf.name)
        try:
            with gzip.open(tpath_c, "wt", encoding="utf-8") as gf:
                gf.write('{"metadata": {"total_seeds": 2}, "records": [{"seed": 0}, {"seed": 1}]')
            with self.assertRaises(RuntimeError) as ctx_c:
                _, gen_c = stream_archive_records(tpath_c)
                for _ in gen_c:
                    pass
            self.assertIn("Truncated archive stream", str(ctx_c.exception))
            self.assertIn("closing object brace '}'", str(ctx_c.exception))
        finally:
            tpath_c.unlink(missing_ok=True)

        # 反例 D: 顶层闭合花括号 } 后包含意外非空白数据
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tf:
            tpath_d = Path(tf.name)
        try:
            with gzip.open(tpath_d, "wt", encoding="utf-8") as gf:
                gf.write('{"metadata": {"total_seeds": 2}, "records": [{"seed": 0}, {"seed": 1}]} extra_corrupted_data')
            with self.assertRaises(RuntimeError) as ctx_d:
                _, gen_d = stream_archive_records(tpath_d)
                for _ in gen_d:
                    pass
            self.assertIn("Corrupted archive stream", str(ctx_d.exception))
            self.assertIn("trailing non-whitespace data", str(ctx_d.exception))
        finally:
            tpath_d.unlink(missing_ok=True)

    def test_04e_negative_duplicate_seed_rejected(self):
        """流式读取负向反例：遇到重复 seed 时必须在加入集合前拒绝并抛出 RuntimeError，严禁假通过。"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as tf:
            tpath = Path(tf.name)
        try:
            # 构造包含重复种子 [0, 1, 1] 的微型归档
            with gzip.open(tpath, "wt", encoding="utf-8") as gf:
                gf.write('{"metadata": {"total_seeds": 2}, "records": [{"seed": 0}, {"seed": 1}, {"seed": 1}]}')
            _, gen = stream_archive_records(tpath)
            seen_seeds = set()
            total_evaluated = 0
            with self.assertRaises(RuntimeError) as ctx:
                for rec in gen:
                    s = rec["seed"]
                    if s in seen_seeds:
                        raise RuntimeError(f"Duplicate seed {s} detected in archive stream at record {total_evaluated + 1}")
                    seen_seeds.add(s)
                    total_evaluated += 1
            self.assertIn("Duplicate seed 1 detected", str(ctx.exception))
        finally:
            tpath.unlink(missing_ok=True)


class TestM5EvidenceVerification(unittest.TestCase):
    """
    共用 M5 归档证据严格校验函数 (verify_m5_archive_evidence) 微型正负向反例测试集：
    覆盖审查员专项提出的所有边界与防篡改反例：
    1. 缺清单文件 (Missing Manifest) -> FileNotFoundError；
    2. 清单非 JSON 字典 (Invalid JSON) -> ValueError；
    3. 清单缺失 auditor_binding -> AssertionError；
    4. 缺归档实物文件 (Missing Archive) -> FileNotFoundError；
    5. 归档文件大小不匹配 (Size Mismatch) -> AssertionError；
    6. 清单 source_archive.sha256 不匹配 (Archive SHA-256 Mismatch) -> AssertionError；
    7. 清单 core_source_digest 不匹配 (Core Digest Mismatch) -> AssertionError；
    8. 当前代码库业务逻辑发生漂移 (Codebase Drift) -> AssertionError；
    9. 质量门禁为空字典 {} (Empty Gates, 审查员实测反例) -> AssertionError；
    10. 质量门禁不完整 (Incomplete Gates) -> AssertionError；
    11. 质量门禁包含失败状态 (Failing Gate) -> AssertionError；
    12. 清单 target_batch_hash 期望值不匹配 -> AssertionError；
    13. 归档流式重算哈希篡改 (Stream Recomputed Hash Mismatch) -> AssertionError；
    14. 归档流中包含重复种子 (Duplicate Seed) -> AssertionError；
    15. 归档流种子数量不足 (Missing Seeds Count) -> AssertionError；
    16. 微型有效证据全流式校验通过 (Micro Valid Evidence Pass) -> 返回 VERIFIED；
    17. 真实生产环境清单与归档元数据快速核验通过 (Real Production Evidence Fast Metadata Pass) -> 返回 VERIFIED。
    """

    @staticmethod
    def _create_mock_m5_evidence(
        tmp_path: Path,
        seeds: list[int] = [0, 1],
        core_digest: str | None = None,
        quality_gates: dict | None = None,
        tamper_stream_hash: bool = False,
        duplicate_seed: bool = False,
        tamper_positive_text: bool = False,
        evaluation_result_override: dict | None = None,
        source_archive_override: dict | None = None,
    ):
        from tests.test_rc8_quality_gate import (
            REQUIRED_M5_QUALITY_GATES,
            compute_current_core_source_digest,
        )
        import hashlib
        if core_digest is None:
            core_digest = compute_current_core_source_digest(REPO_DIR)

        if quality_gates is None:
            quality_gates = {g: "PASS (0 failures)" for g in REQUIRED_M5_QUALITY_GATES}

        archive_path = tmp_path / "mock_archive.json.gz"
        records = []
        ordered_hashes = []
        actual_seeds = list(seeds)
        if duplicate_seed and len(actual_seeds) > 0:
            actual_seeds.append(actual_seeds[0])

        for s in actual_seeds:
            pos_txt = f"mock prompt for seed {s}"
            h = hashlib.sha256(pos_txt.encode("utf-8")).hexdigest()
            ordered_hashes.append((s, h))
            record_txt = (pos_txt + " [tampered]") if (tamper_positive_text and s == actual_seeds[-1]) else pos_txt
            record_h = "0" * 64 if (tamper_stream_hash and s == actual_seeds[-1]) else h
            records.append({
                "seed": s,
                "round_a": {
                    "positive": record_txt,
                    "pos_hash": record_h,
                }
            })

        archive_doc = {
            "metadata": {"total_seeds": len(actual_seeds)},
            "records": records,
        }
        with gzip.open(archive_path, "wt", encoding="utf-8") as gf:
            json.dump(archive_doc, gf)

        arch_bytes = archive_path.read_bytes()
        arch_size = len(arch_bytes)
        arch_sha256 = hashlib.sha256(arch_bytes).hexdigest()

        ordered_hashes.sort(key=lambda x: x[0])
        raw_hash_str = "".join(h for _, h in ordered_hashes)
        expected_batch_hash = hashlib.sha256(raw_hash_str.encode("utf-8")).hexdigest()

        source_archive = {
            "sha256": arch_sha256,
            "size_bytes": arch_size,
            "total_records_processed": len(seeds),
            "contiguous_seed_range": [0, len(seeds) - 1],
        }
        if source_archive_override:
            source_archive.update(source_archive_override)

        evaluation_result = {
            "total_seeds": len(seeds),
            "is_all_explained": True,
            "unexplained_count": 0,
            "unexplained_seeds": {},
            "divergent_count": len(seeds),
            "identical_count": 0,
        }
        if evaluation_result_override:
            evaluation_result.update(evaluation_result_override)

        manifest_doc = {
            "manifest_version": "2.0-reeval",
            "auditor_binding": {
                "baseline_commit": "f1b873c",
                "target_commit": "1db9e67",
                "core_source_digest": core_digest,
                "target_batch_hash": expected_batch_hash,
            },
            "source_archive": source_archive,
            "evaluation_result": evaluation_result,
            "quality_gates": quality_gates,
        }
        manifest_path = tmp_path / "mock_manifest.json"
        manifest_path.write_text(json.dumps(manifest_doc, indent=2), encoding="utf-8")

        return {
            "manifest_path": manifest_path,
            "archive_path": archive_path,
            "arch_size": arch_size,
            "arch_sha256": arch_sha256,
            "golden_hash": expected_batch_hash,
            "core_digest": core_digest,
            "seeds_count": len(seeds),
        }

    def test_01_missing_manifest_rejected(self):
        """反例 1：清单文件不存在，必须 Fail-Closed 抛出 FileNotFoundError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        with self.assertRaises(FileNotFoundError) as ctx:
            verify_m5_archive_evidence(Path("/nonexistent/manifest.json"), Path("/nonexistent/archive.gz"), REPO_DIR)
        self.assertIn("Missing M5 manifest file", str(ctx.exception))

    def test_02_invalid_manifest_json_rejected(self):
        """反例 2：清单不是有效 JSON 字典，必须 Fail-Closed 抛出异常。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            bad_manifest = tmp_path / "manifest.json"
            bad_manifest.write_text("not a valid json doc", encoding="utf-8")
            with self.assertRaises(Exception):
                verify_m5_archive_evidence(bad_manifest, tmp_path / "archive.gz", REPO_DIR)

    def test_03_missing_auditor_binding_rejected(self):
        """反例 3：清单缺失 auditor_binding 对象，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            man = tmp_path / "manifest.json"
            man.write_text(json.dumps({"quality_gates": {}}), encoding="utf-8")
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(man, tmp_path / "archive.gz", REPO_DIR)
            self.assertIn("missing valid 'auditor_binding'", str(ctx.exception))

    def test_04_missing_archive_file_rejected(self):
        """反例 4：清单有效但归档实物文件缺失，必须 Fail-Closed 抛出 FileNotFoundError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path)
            nonexistent_archive = tmp_path / "missing_evidence.json.gz"
            with self.assertRaises(FileNotFoundError) as ctx:
                verify_m5_archive_evidence(env["manifest_path"], nonexistent_archive, REPO_DIR)
            self.assertIn("Missing M5 archive evidence file", str(ctx.exception))

    def test_05_archive_size_mismatch_rejected(self):
        """反例 5：归档实物文件大小与期望大小不匹配，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path)
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"] + 9999,
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Archive file size mismatch", str(ctx.exception))

    def test_06_archive_sha256_mismatch_rejected(self):
        """反例 6：清单中 source_archive.sha256 与期望哈希不匹配，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(
                tmp_path,
                source_archive_override={"sha256": "0" * 64},
            )
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Manifest source_archive.sha256 mismatch", str(ctx.exception))

    def test_07_core_source_digest_mismatch_rejected(self):
        """反例 7：清单中 core_source_digest 与期望核心源码摘要不匹配，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path, core_digest="0" * 64)
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"] + "_drift",
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Manifest core_source_digest mismatch", str(ctx.exception))

    def test_08_current_code_drift_rejected(self):
        """反例 8：当前工作区核心源码被修改（当前 core digest 与 expected_core_digest 不一致），必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path, core_digest="drifted_fake_digest_123")
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest="drifted_fake_digest_123",
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Current codebase core_source_digest drifted", str(ctx.exception))

    def test_09_empty_quality_gates_rejected(self):
        """反例 9（审查员关键反例）：清单中 quality_gates 为空字典 {} 时，严禁真空放行，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path, quality_gates={})
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("quality_gates must be a non-empty dictionary", str(ctx.exception))

    def test_10_incomplete_quality_gates_rejected(self):
        """反例 10：清单中 quality_gates 缺失关键门禁（如缺少 secondary_idempotency），必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence, REQUIRED_M5_QUALITY_GATES
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            incomplete_gates = {g: "PASS (0 failures)" for g in REQUIRED_M5_QUALITY_GATES if g != "secondary_idempotency"}
            env = self._create_mock_m5_evidence(tmp_path, quality_gates=incomplete_gates)
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Incomplete quality gates", str(ctx.exception))
            self.assertIn("secondary_idempotency", str(ctx.exception))

    def test_11_failing_quality_gate_rejected(self):
        """反例 11：清单中某项质量门禁状态未通过（如 'FAIL (1 failure at seed 42)'），必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence, REQUIRED_M5_QUALITY_GATES
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            failing_gates = {g: "PASS (0 failures)" for g in REQUIRED_M5_QUALITY_GATES}
            failing_gates["determinism"] = "FAIL (1 failure at seed 42)"
            env = self._create_mock_m5_evidence(tmp_path, quality_gates=failing_gates)
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Quality gate 'determinism' failed", str(ctx.exception))

    def test_12_target_batch_hash_mismatch_rejected(self):
        """反例 12：清单声明的 target_batch_hash 与期望 golden_hash 不匹配，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path)
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash="mismatched_target_hash_" + "0" * 43,
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Manifest target_batch_hash mismatch", str(ctx.exception))

    def test_13_stream_recompute_batch_hash_mismatch_rejected(self):
        """反例 13：归档实物记录中提示词哈希被篡改，流式逐条重算必失配，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path)
            # 传入与归档实际内容不同的 expected_golden_hash
            tampered_golden = "f" * 64
            # 同时也让清单中的 target_batch_hash 为 tampered_golden
            doc = json.loads(env["manifest_path"].read_text(encoding="utf-8"))
            doc["auditor_binding"]["target_batch_hash"] = tampered_golden
            env["manifest_path"].write_text(json.dumps(doc), encoding="utf-8")

            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=True,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=tampered_golden,
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Streamingly recomputed batch hash mismatch", str(ctx.exception))

    def test_14_stream_recompute_duplicate_seed_rejected(self):
        """反例 14：归档实物流中出现重复种子（如两组 seed 0），流式读取器必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path, seeds=[0, 1], duplicate_seed=True)
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=True,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=2,
                )
            self.assertIn("Duplicate seed 0 found in archive stream", str(ctx.exception))

    def test_15_stream_recompute_missing_seed_count_rejected(self):
        """反例 15：归档实物流产出的种子数量不等于期望数量，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(
                tmp_path,
                seeds=[0],
                source_archive_override={"total_records_processed": 2, "contiguous_seed_range": [0, 1]},
                evaluation_result_override={"total_seeds": 2, "divergent_count": 2},
            )
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=True,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=2,  # 预期 2 种子，但归档只有 1 条记录
                )
            self.assertIn("Archive stream did not yield exact 2 seeds", str(ctx.exception))

    def test_16_micro_positive_valid_evidence_passes(self):
        """正向微型验证：完整合法的微型归档与清单证据，流式重算校验与防篡改反例必须 100% 成功通过。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path, seeds=[0, 1])
            res = verify_m5_archive_evidence(
                env["manifest_path"],
                env["archive_path"],
                REPO_DIR,
                stream_recompute=True,
                expected_archive_size=env["arch_size"],
                expected_archive_sha256=env["arch_sha256"],
                expected_golden_hash=env["golden_hash"],
                expected_core_digest=env["core_digest"],
                expected_seeds_count=env["seeds_count"],
            )
            self.assertEqual(res["status"], "VERIFIED")
            self.assertEqual(res["batch_hash"], env["golden_hash"])
            self.assertEqual(res["total_seeds"], 2)

    def test_17_real_production_evidence_fast_metadata_verified(self):
        """校验历史归档大小、SHA-256、质量门禁及其绑定的历史源码摘要。"""
        from tests.test_rc8_quality_gate import (
            m5_archive_path,
            may_skip_unavailable_m5_archive,
            verify_m5_historical_archive,
            EXPECTED_M5_GOLDEN_HASH,
            EXPECTED_M5_CORE_SOURCE_DIGEST,
        )
        manifest_path = REPO_DIR / "scratch" / "audit_m5_3_reeval_manifest.json"
        archive_path = m5_archive_path(REPO_DIR)
        self.assertTrue(manifest_path.is_file(), "Production M5 reeval manifest must exist")
        if not archive_path.is_file() and may_skip_unavailable_m5_archive(archive_path):
            self.skipTest("Historical M5 archive unavailable; live current-source seed gate runs separately")
        self.assertTrue(archive_path.is_file(), f"Production M5 evidence archive must exist: {archive_path}")

        res = verify_m5_historical_archive(
            manifest_path,
            archive_path,
            REPO_DIR,
            stream_recompute=False,
        )
        self.assertEqual(res["status"], "VERIFIED")
        self.assertEqual(res["batch_hash"], EXPECTED_M5_GOLDEN_HASH)
        self.assertEqual(res["total_seeds"], 10000)
        self.assertEqual(res["core_source_digest"], EXPECTED_M5_CORE_SOURCE_DIGEST)

    def test_17b_real_v1_2_1_production_evidence_fast_metadata_verified(self):
        """生产级元数据快速验证：无条件核验当前 v1.2.1 真实清单与归档实物元数据（大小、SHA-256、门禁、核心源码确定性摘要零漂移）。"""
        from tests.test_rc8_quality_gate import (
            verify_m5_archive_evidence,
            EXPECTED_V1_2_1_GOLDEN_HASH,
            EXPECTED_V1_2_1_CORE_SOURCE_DIGEST,
        )
        manifest_path = REPO_DIR / "scratch" / "audit_v1_2_1_10k_manifest.json"
        archive_path = REPO_DIR / "scratch" / "audit_v1_2_1_10k_evidence.json.gz"
        self.assertTrue(manifest_path.is_file(), "Production v1.2.1 10k manifest must exist")
        self.assertTrue(archive_path.is_file(), "Production v1.2.1 10k evidence archive must exist")

        res = verify_m5_archive_evidence(
            manifest_path,
            archive_path,
            REPO_DIR,
            stream_recompute=False,
        )
        self.assertEqual(res["status"], "VERIFIED")
        self.assertEqual(res["batch_hash"], EXPECTED_V1_2_1_GOLDEN_HASH)
        self.assertEqual(res["total_seeds"], 10000)
        self.assertEqual(res["core_source_digest"], EXPECTED_V1_2_1_CORE_SOURCE_DIGEST)

    def test_18_negative_tampered_archive_file_content_fails_sha256(self):
        """反例 18（审查员关键反例）：直接篡改归档实物文件内容（即便是微小单字节篡改、清单声明未变），实算 SHA-256 必须 Fail-Closed 拦截。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path)
            # 直接篡改归档文件在磁盘上的内容（保持大小不变或追加）
            with open(env["archive_path"], "r+b") as f:
                f.seek(10)
                byte_val = f.read(1)
                new_byte = bytes([(byte_val[0] ^ 0xFF)])
                f.seek(10)
                f.write(new_byte)

            # 快速模式必须立即拦截
            with self.assertRaises(AssertionError) as ctx_fast:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Archive file real SHA-256 mismatch", str(ctx_fast.exception))

            # 流式模式也必须在开头立即被实物 SHA-256 拦截
            with self.assertRaises(AssertionError) as ctx_stream:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=True,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("Archive file real SHA-256 mismatch", str(ctx_stream.exception))

    def test_19_negative_tampered_positive_text_fails_streaming_pos_hash(self):
        """反例 19（审查员关键反例）：归档记录中修改了 positive 文本但保持旧 pos_hash，流式重算必须对每条记录重新计算摘要并拦截失配。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            # 构造包含篡改文本但保留原 pos_hash 的归档
            env = self._create_mock_m5_evidence(
                tmp_path,
                seeds=[0, 1],
                tamper_positive_text=True,
                tamper_stream_hash=False,  # 保持旧 pos_hash 不变
            )
            # 流式分支必须通过逐条重算 positive 发现 stored pos_hash 与 computed 不匹配
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=True,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("stored pos_hash mismatch", str(ctx.exception))

    def test_20_negative_unexplained_count_greater_than_zero_rejected(self):
        """反例 20（审查员关键反例）：清单中 evaluation_result.unexplained_count > 0 时，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(
                tmp_path,
                evaluation_result_override={"unexplained_count": 1},
            )
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("unexplained_count is non-zero", str(ctx.exception))

    def test_21_negative_is_all_explained_false_rejected(self):
        """反例 21（审查员关键反例）：清单中 evaluation_result.is_all_explained 为 False 时，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(
                tmp_path,
                evaluation_result_override={"is_all_explained": False},
            )
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("is_all_explained is not True", str(ctx.exception))

    def test_22_negative_unexplained_seeds_not_empty_rejected(self):
        """反例 22：清单中 evaluation_result.unexplained_seeds 包含失败种子明细时，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(
                tmp_path,
                evaluation_result_override={"unexplained_seeds": {"42": "failed"}},
            )
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("unexplained_seeds must be empty", str(ctx.exception))

    def test_23_negative_missing_evaluation_result_rejected(self):
        """反例 23：清单缺失 evaluation_result 对象时，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(tmp_path)
            # 从清单中删除 evaluation_result
            doc = json.loads(env["manifest_path"].read_text(encoding="utf-8"))
            del doc["evaluation_result"]
            env["manifest_path"].write_text(json.dumps(doc), encoding="utf-8")

            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("missing valid 'evaluation_result' object", str(ctx.exception))

    def test_24_negative_missing_or_mismatched_source_archive_range_rejected(self):
        """反例 24：清单中 source_archive 种子范围或处理记录数与预期不符时，必须 Fail-Closed 抛出 AssertionError。"""
        from tests.test_rc8_quality_gate import verify_m5_archive_evidence
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env = self._create_mock_m5_evidence(
                tmp_path,
                source_archive_override={"contiguous_seed_range": [0, 9999]},  # 预期是 [0, 1]
            )
            with self.assertRaises(AssertionError) as ctx:
                verify_m5_archive_evidence(
                    env["manifest_path"],
                    env["archive_path"],
                    REPO_DIR,
                    stream_recompute=False,
                    expected_archive_size=env["arch_size"],
                    expected_archive_sha256=env["arch_sha256"],
                    expected_golden_hash=env["golden_hash"],
                    expected_core_digest=env["core_digest"],
                    expected_seeds_count=env["seeds_count"],
                )
            self.assertIn("contiguous_seed_range mismatch", str(ctx.exception))

    def test_25_rc8_quality_gate_class_discovery_intact(self):
        """测试类发现完整性验证（审查员问题 3 修复核验）：确保 TestRC8QualityGate 完整暴露且发现全部 7 项测试方法。"""
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromName("tests.test_rc8_quality_gate")
        def collect_test_ids(s):
            ids = []
            for item in s:
                if isinstance(item, unittest.TestSuite):
                    ids.extend(collect_test_ids(item))
                else:
                    ids.append(item.id())
            return ids

        discovered = collect_test_ids(suite)
        expected_tests = [
            "tests.test_rc8_quality_gate.TestRC8QualityGate.test_01_rc7_baseline_28_cases_quality_metrics",
            "tests.test_rc8_quality_gate.TestRC8QualityGate.test_01b_v1_2_1_28_cases_live_quality_gate",
            "tests.test_rc8_quality_gate.TestRC8QualityGate.test_02_all_77_presets_and_8_recipes_full_matrix",
            "tests.test_rc8_quality_gate.TestRC8QualityGate.test_03_seed_gate_functional_determinism",
            "tests.test_rc8_quality_gate.TestRC8QualityGate.test_03b_seed_gate_golden_hash",
            "tests.test_rc8_quality_gate.TestRC8QualityGate.test_03c_dual_version_divergence_audit",
            "tests.test_rc8_quality_gate.TestRC8QualityGate.test_04_oracle_negative_mutations",
        ]
        for exp in expected_tests:
            self.assertIn(exp, discovered, f"Missing expected test in TestRC8QualityGate discovery: {exp}")
        self.assertEqual(len(discovered), 7, f"Expected exactly 7 tests in TestRC8QualityGate, got: {discovered}")


class TestV121CrossVersionAttribution(unittest.TestCase):
    """v1.2.0 -> v1.2.1 逐槽位、逐词条确定性重放归因引擎正负向门禁测试（禁止无依据下游放行）。"""

    @classmethod
    def setUpClass(cls):
        from scratch.run_v1_2_1_10k_audit import (
            DEFAULT_INPUTS,
            _worker_10k_chunk,
        )

        cls.cur_seed0 = _worker_10k_chunk((0, 1))[0]
        diag = nodes.IYKYKPromptDiagnostics()
        _, _, _, diag_s = diag.diagnose(**DEFAULT_INPUTS, prompt_seed=0)
        cls.diag0_obj = json.loads(diag_s)

        # 基于真实 seed 0 构造合法可重放的 v1.2.0 基准对比样本（不依赖 737MB 本地外部归档即可在任意干净签出下执行）
        sels_v120 = copy.deepcopy(cls.diag0_obj["selections"])
        # 模拟 nudity L6 单分支收敛、shot_type 跨槽位候选过滤（v1.2.0 为 medium_shot）、以及 context/dist 变化
        for sel in sels_v120:
            if sel["selector"] == "nudity":
                sel["accepted_atoms"] = [{"text": "spread legs"}, {"text": "exposed genitals"}]
            elif sel["selector"] == "shot_type":
                sel["selected_id"] = "medium_shot"
                sel["accepted_atoms"] = [{"text": "medium shot"}]
                rat = sel.setdefault("sampling_rationale", {})
                rat["candidate_filter"] = {
                    "excluded_hard_conflicts": [],
                    "passed_candidates": 16,
                    "total_candidates": 16,
                }
        pos_v120 = ", ".join(
            a["text"] for s in sels_v120 for a in s.get("accepted_atoms", []) if a.get("text")
        )
        cls.synthetic_v120_seed0 = {
            "positive": pos_v120,
            "diagnostics_audit": {"selections": sels_v120},
            "source_atoms": [{"source_slot": "nudity", "source_item_id": "nudity_l6"}],
            "decisions": list(cls.cur_seed0["attr_ctx"]["decisions"]),
            "final_atoms": [{"text": a["text"]} for s in sels_v120 for a in s.get("accepted_atoms", [])],
        }

        # 若本地存在真实 737MB v1.2.0 基准归档，额外加载真实关键种子验证真实历史样本
        cls.real_v120_records = {}
        cls.real_cur_records = {}
        v120_arch = REPO_DIR / "scratch" / "audit_m5_3_evidence.json.gz"
        if v120_arch.is_file():
            target_seeds = {0, 2, 4, 24, 27, 306, 863, 1518}
            for s in sorted(target_seeds):
                cls.real_cur_records[s] = cls.cur_seed0 if s == 0 else _worker_10k_chunk((s, 1))[0]
            _, rec_it = stream_archive_records(v120_arch)
            for rec in rec_it:
                s = rec["seed"]
                if s in target_seeds:
                    cls.real_v120_records[s] = rec.get("round_a", {})
                    if len(cls.real_v120_records) == len(target_seeds):
                        break

    def test_01_positive_legitimate_replay_and_downstream_distribution_shifts_verified(self):
        """正向验证：真实上游收敛 + 跨槽位候选/分布偏移通过逐槽位 PRNG 子流确定性重放验证，杜绝无名放行。"""
        from scratch.run_v1_2_1_10k_audit import attribute_v120_vs_v121_seed

        attr = attribute_v120_vs_v121_seed(self.synthetic_v120_seed0, self.cur_seed0)
        self.assertTrue(attr["is_divergent"])
        self.assertTrue(attr["is_explained"], f"Expected explained, got: {attr.get('unexplained_reason')}")
        self.assertNotIn("downstream_prng_stream_shift", attr["verified_causes"])
        slot_map = {sv["slot_key"]: sv for sv in attr["slot_verifications"]}
        self.assertIn("shot_type", slot_map)
        self.assertIn("nudity", slot_map)
        for sv in attr["slot_verifications"]:
            self.assertTrue(sv["replay_verified"])
            self.assertTrue(sv["valid_candidate"])

        # 若本地存在 v1.2.0 归档，同步复核真实跨版本样本（含子标签过滤 306/863/1518 与受控重组 24、上下文/分布变化 27）
        for sd, v120_ra in self.real_v120_records.items():
            real_attr = attribute_v120_vs_v121_seed(v120_ra, self.real_cur_records[sd])
            self.assertTrue(
                real_attr["is_explained"],
                f"Real v1.2.0->v1.2.1 seed {sd} failed attribution: {real_attr.get('unexplained_reason')}",
            )
            self.assertNotIn("downstream_prng_stream_shift", real_attr["verified_causes"])
            self.assertGreater(len(real_attr["slot_verifications"]), 0)
            for sv in real_attr["slot_verifications"]:
                self.assertTrue(sv["replay_verified"], f"Seed {sd} slot {sv['slot_key']} missing replay_verified")
                self.assertTrue(sv["valid_candidate"], f"Seed {sd} slot {sv['slot_key']} missing valid_candidate")

    def test_02_negative_unbacked_sentinel_injected_in_quality_hairstyle_pose_rejected(self):
        """审核员反例复现：向 quality / hairstyle / pose 注入 UNBACKED_AUDIT_SENTINEL 且同步改写 positive 与 final_texts 时，即使上游已有合法分叉，也必须 Fail-Closed 返回 is_explained=False。"""
        from scratch.run_v1_2_1_10k_audit import attribute_v120_vs_v121_seed

        v120_baselines = [self.synthetic_v120_seed0]
        if 0 in self.real_v120_records:
            v120_baselines.append(self.real_v120_records[0])

        for v120_base in v120_baselines:
            for target_sel in ("quality", "hairstyle", "pose"):
                mutated = copy.deepcopy(self.cur_seed0)
                mutated["positive"] = mutated["positive"] + ", UNBACKED_AUDIT_SENTINEL"
                mutated["attr_ctx"]["final_texts"].append("UNBACKED_AUDIT_SENTINEL")
                replaced = False
                for sel in mutated["attr_ctx"]["selections"]:
                    if sel["selector"] == target_sel and sel.get("occ", 0) == 0:
                        sel["accepted_texts"].append("UNBACKED_AUDIT_SENTINEL")
                        replaced = True
                        break
                self.assertTrue(replaced, f"Selector {target_sel} not found in cur_seed0")

                attr = attribute_v120_vs_v121_seed(v120_base, mutated)
                self.assertFalse(
                    attr["is_explained"],
                    f"Injected UNBACKED_AUDIT_SENTINEL into {target_sel!r} MUST NOT be explained! Got: {attr}",
                )
                self.assertIn("UNBACKED_AUDIT_SENTINEL", attr["unexplained_reason"] or "")

    def test_03_negative_tampered_downstream_selected_id_and_integrity_rejected(self):
        """负向反例：即使上游存在合法分叉，篡改下游 selected_id（如 hairstyle -> high_ponytail）或破坏 positive/final_texts/accepted_texts 闭环一致性，必须被确定性重放和一致性校验拦截。"""
        from scratch.run_v1_2_1_10k_audit import attribute_v120_vs_v121_seed

        # 反例 A：下游 selected_id 被篡改为其他合法词库 ID 且不匹配该种子的 PRNG 子流重放
        tampered_id = copy.deepcopy(self.cur_seed0)
        for sel in tampered_id["attr_ctx"]["selections"]:
            if sel["selector"] == "hairstyle" and sel.get("occ", 0) == 0:
                sel["selected_id"] = "high_ponytail"
                break
        attr_id = attribute_v120_vs_v121_seed(self.synthetic_v120_seed0, tampered_id)
        self.assertFalse(attr_id["is_explained"])
        self.assertIn("Replay selected_id mismatch for hairstyle", attr_id["unexplained_reason"] or "")

        # 反例 B：positive 与 final_texts 不同步（孤立在 positive 追加词条）
        tampered_pos = copy.deepcopy(self.cur_seed0)
        tampered_pos["positive"] = tampered_pos["positive"] + ", ORPHAN_POSITIVE_TOKEN"
        attr_pos = attribute_v120_vs_v121_seed(self.synthetic_v120_seed0, tampered_pos)
        self.assertFalse(attr_pos["is_explained"])
        self.assertIn("integrity check failed", attr_pos["unexplained_reason"] or "")

        # 反例 C：accepted_texts 与 final_texts 不同步
        tampered_fin = copy.deepcopy(self.cur_seed0)
        tampered_fin["attr_ctx"]["final_texts"].append("ORPHAN_FINAL_TOKEN")
        attr_fin = attribute_v120_vs_v121_seed(self.synthetic_v120_seed0, tampered_fin)
        self.assertFalse(attr_fin["is_explained"])
        self.assertIn("accepted_vs_final_mismatch", attr_fin["unexplained_reason"] or "")

    def test_04_archived_v121_evidence_per_seed_attribution_completeness(self):
        """万种子归档证据中必须完整保留逐种子归因结果与重放证明字段，且零 unexplained、零无依据下游放行。"""
        arch_path = REPO_DIR / "scratch" / "audit_v1_2_1_10k_evidence.json.gz"
        self.assertTrue(arch_path.is_file(), f"Missing v1.2.1 archive at {arch_path}")
        _, rec_iter = stream_archive_records(arch_path)
        count = 0
        key_seed_checks = {}
        for rec in rec_iter:
            count += 1
            v_attr = rec.get("v120_attribution")
            self.assertIsInstance(v_attr, dict, f"Seed {rec.get('seed')} missing v120_attribution")
            self.assertTrue(v_attr.get("is_explained"), f"Seed {rec.get('seed')} not explained in archive")
            self.assertTrue(
                v_attr.get("current_replay_verified"),
                f"Seed {rec.get('seed')} missing current_replay_verified in archive",
            )
            self.assertNotIn("downstream_prng_stream_shift", v_attr.get("verified_causes", []))
            if rec.get("seed") in (0, 24, 27, 306, 863, 1518):
                key_seed_checks[rec["seed"]] = v_attr
        self.assertEqual(count, 10000)
        self.assertEqual(set(key_seed_checks.keys()), {0, 24, 27, 306, 863, 1518})
        for sd, v_attr in key_seed_checks.items():
            svs = v_attr.get("slot_verifications", [])
            self.assertGreater(len(svs), 0, f"Seed {sd} has empty slot_verifications")
            for sv in svs:
                self.assertTrue(sv.get("replay_verified"), f"Seed {sd} slot {sv.get('slot_key')} unverified")
                self.assertTrue(sv.get("valid_candidate"), f"Seed {sd} slot {sv.get('slot_key')} invalid candidate")
                self.assertTrue(sv.get("cause"), f"Seed {sd} slot {sv.get('slot_key')} missing cause")

    def test_05_negative_unbacked_omission_of_v121_tag_and_excess_omission_rejected(self):
        """审核员反例闭环：无依据删除 v1.2.1 新增/保留词条、在相同正文中删除词条、用单条决策虚报多条删除、伪造去重记录、或删光非空槽位，均必须返回 is_explained=False。"""
        import hashlib
        from scratch.run_v1_2_1_10k_audit import attribute_v120_vs_v121_seed

        # 反例 A：精确复现审核员 seed 0 漏洞 —— 删除 nudity.accepted_texts[0]，同步更新 final_texts/positive/pos_hash，但不加任何删除/预算/去重记录
        mut_nud = copy.deepcopy(self.cur_seed0)
        removed_nud_txt = None
        for sel in mut_nud["attr_ctx"]["selections"]:
            if sel["selector"] == "nudity" and sel.get("occ", 0) == 0:
                self.assertGreater(len(sel["accepted_texts"]), 0)
                removed_nud_txt = sel["accepted_texts"].pop(0)
                break
        self.assertIsNotNone(removed_nud_txt)
        mut_nud["attr_ctx"]["final_texts"].remove(removed_nud_txt)
        mut_nud["positive"] = ", ".join(mut_nud["attr_ctx"]["final_texts"])
        mut_nud["pos_hash"] = hashlib.sha256(mut_nud["positive"].encode("utf-8")).hexdigest()

        v120_baselines = [self.synthetic_v120_seed0]
        if 0 in self.real_v120_records:
            v120_baselines.append(self.real_v120_records[0])
        for v120_base in v120_baselines:
            attr_rev = attribute_v120_vs_v121_seed(v120_base, mut_nud)
            self.assertFalse(attr_rev["is_explained"], f"Expected failure on unbacked deletion, got: {attr_rev}")
            self.assertFalse(attr_rev.get("current_replay_verified"))
            self.assertEqual(attr_rev.get("primary_cause"), "UNBACKED_OMISSION_FROM_REPLAY")
            self.assertIn(removed_nud_txt, attr_rev.get("unexplained_reason") or "")

        # 反例 B：即使 v120 与 v121 positive 文本一致，也必须先经过确定性重放检查，禁止放行无依据删除
        identical_mut_v120 = {"positive": mut_nud["positive"]}
        attr_ident_bypass = attribute_v120_vs_v121_seed(identical_mut_v120, mut_nud)
        self.assertFalse(attr_ident_bypass["is_explained"])
        self.assertFalse(attr_ident_bypass.get("current_replay_verified"))
        self.assertEqual(attr_ident_bypass.get("primary_cause"), "UNBACKED_OMISSION_FROM_REPLAY")

        # 反例 C：1 条 drop 决策只能核销 1 条删除，删去同槽位全部 2 条词条而只补 1 条 drop 必须拦截
        mut_multi = copy.deepcopy(self.cur_seed0)
        dropped_two = []
        for sel in mut_multi["attr_ctx"]["selections"]:
            if sel["selector"] == "nudity" and sel.get("occ", 0) == 0:
                dropped_two = list(sel["accepted_texts"])
                sel["accepted_texts"] = []
                break
        self.assertGreaterEqual(len(dropped_two), 2)
        for dt in dropped_two:
            mut_multi["attr_ctx"]["final_texts"].remove(dt)
        mut_multi["positive"] = ", ".join(mut_multi["attr_ctx"]["final_texts"])
        mut_multi["pos_hash"] = hashlib.sha256(mut_multi["positive"].encode("utf-8")).hexdigest()
        mut_multi["attr_ctx"]["decisions"].append({
            "rule_id": "synthetic_single_drop",
            "action": "drop",
            "reason_code": "test_single_drop",
            "before_text": dropped_two[0],
            "after_text": None,
            "target_selector": "nudity",
        })
        attr_multi = attribute_v120_vs_v121_seed(self.synthetic_v120_seed0, mut_multi)
        self.assertFalse(attr_multi["is_explained"])
        self.assertIn(dropped_two[1], attr_multi.get("unexplained_reason") or "")

        # 反例 D：对唯一词条伪造 deduplicated_texts（无真实保留副本）必须被完整性检查拦截
        mut_fake_dedup = copy.deepcopy(mut_nud)
        for sel in mut_fake_dedup["attr_ctx"]["selections"]:
            if sel["selector"] == "nudity" and sel.get("occ", 0) == 0:
                sel["deduplicated_texts"].append(removed_nud_txt)
                break
        attr_fake_dedup = attribute_v120_vs_v121_seed(self.synthetic_v120_seed0, mut_fake_dedup)
        self.assertFalse(attr_fake_dedup["is_explained"])
        self.assertEqual(attr_fake_dedup.get("primary_cause"), "UNBACKED_AUDIT_INTEGRITY_MISMATCH")

        # 反例 E：直接从 selections（并同步从 final_texts/positive/pos_hash）移除整个非空槽位
        mut_drop_slot = copy.deepcopy(self.cur_seed0)
        nud_all_txts = []
        kept_sels = []
        for sel in mut_drop_slot["attr_ctx"]["selections"]:
            if sel["selector"] == "nudity" and sel.get("occ", 0) == 0:
                nud_all_txts = list(sel["accepted_texts"])
                continue
            kept_sels.append(sel)
        mut_drop_slot["attr_ctx"]["selections"] = kept_sels
        for nt in nud_all_txts:
            mut_drop_slot["attr_ctx"]["final_texts"].remove(nt)
        mut_drop_slot["positive"] = ", ".join(mut_drop_slot["attr_ctx"]["final_texts"])
        mut_drop_slot["pos_hash"] = hashlib.sha256(mut_drop_slot["positive"].encode("utf-8")).hexdigest()
        attr_drop_slot = attribute_v120_vs_v121_seed(self.synthetic_v120_seed0, mut_drop_slot)
        self.assertFalse(attr_drop_slot["is_explained"])
        self.assertEqual(attr_drop_slot.get("primary_cause"), "UNBACKED_OMISSION_FROM_REPLAY")

    def test_06_positive_legitimate_dedup_resolver_and_budget_omissions_verified(self):
        """正向核验：真实的去重、冲突消解 drop/inject、以及有真实 budget_filtered_texts 凭证的预算裁剪均须通过重放多重集守恒核验。"""
        import hashlib
        from scratch.run_v1_2_1_10k_audit import (
            _extract_positional_v121,
            _verify_current_replay_consistency,
            _worker_10k_chunk,
            attribute_v120_vs_v121_seed,
        )

        # 1. 真实 seed 0（包含真实冲突消解 drop + inject 决策）
        ctx0 = self.cur_seed0["attr_ctx"]
        sels0, map0 = _extract_positional_v121(ctx0)
        rp0, err0 = _verify_current_replay_consistency(
            self.cur_seed0, sels0, map0, list(ctx0["final_texts"]), self.cur_seed0["positive"], list(ctx0["decisions"])
        )
        self.assertIsNone(err0, f"Seed 0 replay consistency failed: {err0}")
        self.assertIsNotNone(rp0)

        # 2. 真实去重种子 seed 8 与 seed 11（clothing_state 与 nudity 共享 'completely naked' 触发真实去重）
        for sd in (8, 11):
            cur_rec = _worker_10k_chunk((sd, 1))[0]
            ctx_sd = cur_rec["attr_ctx"]
            dedup_sels = [s for s in ctx_sd["selections"] if s.get("deduplicated_texts")]
            self.assertGreater(len(dedup_sels), 0, f"Expected real deduplicated_texts on seed {sd}")
            sels_sd, map_sd = _extract_positional_v121(ctx_sd)
            rp_sd, err_sd = _verify_current_replay_consistency(
                cur_rec, sels_sd, map_sd, list(ctx_sd["final_texts"]), cur_rec["positive"], list(ctx_sd["decisions"])
            )
            self.assertIsNone(err_sd, f"Seed {sd} dedup replay consistency failed: {err_sd}")
            self.assertIsNotNone(rp_sd)
            attr_self = attribute_v120_vs_v121_seed({"positive": cur_rec["positive"]}, cur_rec)
            self.assertTrue(attr_self["is_explained"])
            self.assertTrue(attr_self.get("current_replay_verified"))

        # 3. 真实尾部普通采样槽位（如 props）预算裁剪记录：有 budget_filtered_texts 支撑时通过，撤除记录立即拒绝
        cur_budget = copy.deepcopy(self.cur_seed0)
        trimmed_txt = None
        for sel in reversed(cur_budget["attr_ctx"]["selections"]):
            if sel.get("accepted_texts") and sel["selector"] in ("props", "liquids", "film", "quality"):
                trimmed_txt = sel["accepted_texts"].pop()
                sel["budget_filtered_texts"].append(trimmed_txt)
                break
        self.assertIsNotNone(trimmed_txt)
        idx_last = len(cur_budget["attr_ctx"]["final_texts"]) - 1 - cur_budget["attr_ctx"]["final_texts"][::-1].index(trimmed_txt)
        cur_budget["attr_ctx"]["final_texts"].pop(idx_last)
        cur_budget["positive"] = ", ".join(cur_budget["attr_ctx"]["final_texts"])
        cur_budget["pos_hash"] = hashlib.sha256(cur_budget["positive"].encode("utf-8")).hexdigest()

        sels_b, map_b = _extract_positional_v121(cur_budget["attr_ctx"])
        _, err_b = _verify_current_replay_consistency(
            cur_budget,
            sels_b,
            map_b,
            list(cur_budget["attr_ctx"]["final_texts"]),
            cur_budget["positive"],
            list(cur_budget["attr_ctx"]["decisions"]),
        )
        self.assertIsNone(err_b, f"Backed budget omission should pass: {err_b}")

        # 撤除 budget_filtered_texts 后立即拒绝
        for sel in cur_budget["attr_ctx"]["selections"]:
            if trimmed_txt in sel.get("budget_filtered_texts", []):
                sel["budget_filtered_texts"].remove(trimmed_txt)
        sels_nb, map_nb = _extract_positional_v121(cur_budget["attr_ctx"])
        _, err_nb = _verify_current_replay_consistency(
            cur_budget,
            sels_nb,
            map_nb,
            list(cur_budget["attr_ctx"]["final_texts"]),
            cur_budget["positive"],
            list(cur_budget["attr_ctx"]["decisions"]),
        )
        self.assertIsNotNone(err_nb)
        self.assertEqual(err_nb.get("primary_cause"), "UNBACKED_OMISSION_FROM_REPLAY")


if __name__ == "__main__":
    unittest.main()
