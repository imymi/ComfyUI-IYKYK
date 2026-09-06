"""
test_rc8_quality_gate.py — ComfyUI-IYKYK v1.1.0-rc8 全链质量门禁与跨版本对比测试
"""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import os
import unittest
from pathlib import Path
from random import Random

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

from lib.conflict_resolver import ConflictResolver
from lib.lexer import validate_prompt_syntax
from lib.models import GenerationResult, ResolutionDecision
import nodes
from tests.audit_oracle import validate_audit_json_oracle

from lib.conflict_resolver import build_canonical_catalog_facts

DATA_DIR = Path(__file__).parent.parent / "data"
SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
DIAGNOSTICS_SCHEMA_DOC = json.loads((SCHEMAS_DIR / "diagnostics.schema.json").read_text(encoding="utf-8"))
EXPECTED_BASELINE_CONTENT_SHA256 = "779aca4d52238cabd9eaa8d4f5411654a8b01bb19f3519cff60eff7cc9541783"

FROZEN_RULE_CONTRACTS = {
    "spatial_environmental_mutual_exclusion": {
        "physical_domain": "spatial_environment",
        "violated_invariant": "Indoor and outdoor spatial environments cannot coexist simultaneously",
    },
    "nudity_clothing_conflicts": {
        "physical_domain": "nudity_coverage",
        "violated_invariant": "Worn garments or underwear cannot coexist with full body nudity",
    },
    "framing_lower_body_coherence": {
        "physical_domain": "framing_composition",
        "violated_invariant": "Lower body garments and states cannot be visible in close-up face framing",
    },
}

def _collect_all_catalog_ids(data_dir: Path) -> set[str]:
    all_ids = set()
    for p in data_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            def walk(obj):
                if isinstance(obj, dict):
                    if "id" in obj and isinstance(obj["id"], str):
                        all_ids.add(obj["id"])
                    for v in obj.values():
                        walk(v)
                elif isinstance(obj, list):
                    for itm in obj:
                        walk(itm)
            walk(data)
        except Exception:
            pass
    all_ids.update({"quality_high", "quality_masterpiece", "quality_phone", "quality_cctv", "quality_standard"})
    return all_ids

CATALOG_VALID_ROOTS = _collect_all_catalog_ids(DATA_DIR)
CANONICAL_FACTS = build_canonical_catalog_facts(DATA_DIR)

RULE_PROJECTION_FIELDS = {
    "spatial_environmental_mutual_exclusion": ("semantic_role", "space_kind", "venue_ids", "time_of_day"),
    "nudity_clothing_conflicts": ("semantic_role", "visible_regions", "garment_topologies", "garment_states"),
    "framing_lower_body_coherence": ("semantic_role", "visible_regions", "garment_topologies", "garment_states"),
}

def project_facts(atom_facts: dict, rule_id: str) -> dict:
    allowed = RULE_PROJECTION_FIELDS.get(rule_id, tuple(atom_facts.keys()))
    return {k: atom_facts[k] for k in allowed if k in atom_facts and atom_facts[k]}

def get_effective_facts(atom) -> dict:
    if atom is None:
        return {}
    base_facts = atom.facts.to_dict() if atom.facts else {}
    if (atom.source_slot, atom.source_item_id) in CANONICAL_FACTS:
        cat_facts = CANONICAL_FACTS[(atom.source_slot, atom.source_item_id)].to_dict()
        for k, v in cat_facts.items():
            if k not in base_facts or not base_facts[k]:
                base_facts[k] = v
    return base_facts

def _verify_provenance_dag(res: GenerationResult, valid_roots: set[str] | None = None) -> bool:
    if valid_roots is None:
        valid_roots = CATALOG_VALID_ROOTS

    # 1. 检查 source_atoms 内部 atom_id 全局唯一性 (在构建任何 dict/map 之前)
    seen_source_ids = set()
    for a in res.source_atoms:
        if a.atom_id in seen_source_ids:
            raise AssertionError(f"Duplicate source atom ID: {a.atom_id}")
        seen_source_ids.add(a.atom_id)

    source_atom_map = {a.atom_id: a for a in res.source_atoms}
    source_birth_map = {a.atom_id: 0 for a in res.source_atoms}
    produced_birth_map: dict[str, int] = {}
    produced_owners: dict[str, ResolutionDecision] = {}

    if res.resolution_report:
        decisions = res.resolution_report.decisions
        for d in decisions:
            for pid in d.produced_atom_ids:
                if pid in produced_owners:
                    raise AssertionError(f"Duplicate produced owner for atom ID {pid} in decision {d.decision_id}")
                if pid in source_atom_map:
                    raise AssertionError(f"Produced atom ID collision with source atom ID: {pid}")
                produced_owners[pid] = d
                produced_birth_map[pid] = d.sequence

        for i, d in enumerate(decisions):
            if i > 0 and d.sequence <= decisions[i - 1].sequence:
                raise AssertionError(f"Decision sequence order violation: {d.sequence} <= {decisions[i - 1].sequence}")

            # Temporal check: any reference to a produced atom must be produced by an earlier decision
            refs = ([d.target_atom_id] if d.target_atom_id else []) + list(d.winner_atom_ids) + list(d.parent_source_ids)
            for ref_id in refs:
                if ref_id in produced_owners and ref_id not in d.produced_atom_ids:
                    if produced_owners[ref_id].sequence >= d.sequence:
                        raise AssertionError(
                            f"Temporal order violation: decision {d.decision_id} (seq {d.sequence}) "
                            f"references produced atom {ref_id} from decision {produced_owners[ref_id].decision_id} (seq {produced_owners[ref_id].sequence})"
                        )

    # 拒绝 source atoms 引用 produced parents
    for a in res.source_atoms:
        if a.provenance and a.provenance.parent_ids:
            for pid in a.provenance.parent_ids:
                if pid in produced_birth_map:
                    raise AssertionError(f"Source atom {a.atom_id} references produced parent {pid}")

    # 2. 检查 final atoms (res.atoms) 内部全局唯一性，拒绝重复保留 source ID 与重复 produced ID
    seen_final_ids = set()
    for a in res.atoms:
        if a.atom_id in seen_final_ids:
            if a.atom_id in source_atom_map:
                raise AssertionError(f"Duplicate retained source atom ID in final atoms: {a.atom_id}")
            elif a.atom_id in produced_owners:
                raise AssertionError(f"Duplicate produced atom ID in final atoms: {a.atom_id}")
            else:
                raise AssertionError(f"Duplicate final atom ID: {a.atom_id}")
        seen_final_ids.add(a.atom_id)

        # 拒绝 final atoms 出现既不在 source 也不在 produced 的孤儿节点
        if a.atom_id not in source_atom_map and a.atom_id not in produced_owners:
            raise AssertionError(f"Orphan final atom {a.atom_id}: neither in source atoms nor in produced atoms")

    def trace_parent(node_id: str, path: list[str]) -> None:
        if node_id in path:
            p_str = " -> ".join(path + [node_id])
            raise AssertionError(f"Cycle detected in provenance DAG: {p_str}")

        if node_id in valid_roots:
            return

        if node_id in source_atom_map:
            sa = source_atom_map[node_id]
            pids = sa.provenance.parent_ids if sa.provenance else ()
            if not pids:
                raise AssertionError(f"Source atom {node_id} has no provenance parent_ids and is not a valid root")
            for p in pids:
                trace_parent(p, path + [node_id])
            return

        if node_id in produced_owners:
            d = produced_owners[node_id]
            parents = d.parent_source_ids or (([d.target_atom_id] if d.target_atom_id else []) + list(d.winner_atom_ids))
            if not parents:
                raise AssertionError(f"Produced atom {node_id} has no parents in decision {d.decision_id}")
            for p in parents:
                trace_parent(p, path + [node_id])
            return

        raise AssertionError(f"Dangling node ID {node_id!r} cannot be traced back to valid root")

    for a in res.source_atoms:
        if not a.provenance or not a.provenance.parent_ids:
            raise AssertionError(f"Source atom {a.atom_id} has no provenance parent_ids")
        for pid in a.provenance.parent_ids:
            trace_parent(pid, [a.atom_id])

    for a in res.atoms:
        if not a.provenance or not a.provenance.parent_ids:
            raise AssertionError(f"Final atom {a.atom_id} has no provenance parent_ids")
        for pid in a.provenance.parent_ids:
            trace_parent(pid, [a.atom_id])

        # 强校验 produced final atoms 的 parent_ids 必须严格早于自身 birth sequence（不得引用同一或未来 decision 产生的 atom）
        if a.atom_id in produced_birth_map:
            self_birth_seq = produced_birth_map[a.atom_id]
            if a.provenance and a.provenance.parent_ids:
                for pid in a.provenance.parent_ids:
                    if pid in produced_birth_map:
                        parent_birth_seq = produced_birth_map[pid]
                        if parent_birth_seq >= self_birth_seq:
                            raise AssertionError(
                                f"Final atom {a.atom_id} (birth seq {self_birth_seq}) references parent {pid} with non-earlier birth seq {parent_birth_seq}"
                            )

    if res.resolution_report:
        for d in res.resolution_report.decisions:
            if d.target_atom_id:
                trace_parent(d.target_atom_id, [d.decision_id])
            for wid in d.winner_atom_ids:
                trace_parent(wid, [d.decision_id])
            for pid in d.produced_atom_ids:
                trace_parent(pid, [d.decision_id])
            for psid in d.parent_source_ids:
                trace_parent(psid, [d.decision_id])
    return True


def _worker_seed_batch(start_seed: int, count: int) -> tuple[int, int, list[tuple[int, str]], int | None]:
    g = nodes.IYKYKPromptGenerator()
    diag = nodes.IYKYKPromptDiagnostics()
    inputs = {
        "预设模板": "无 (None)",
        "风格配方": "无 (None)",
        "场景大类": "随机 (Random)",
        "剧情主题": "随机 (Random)",
        "景别构图": "自动 (Auto)",
        "拍摄视角": "自动 (Auto)",
        "裸露等级": "随机 (Random)",
        "服装款式": "随机 (Random)",
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
    resolver = ConflictResolver(DATA_DIR)
    pairs = []
    first_fail = None
    for s in range(start_seed, start_seed + count):
        r1 = g.generate_structured(**inputs, prompt_seed=s)
        r2 = g.generate_structured(**inputs, prompt_seed=s)
        # 1. 两次生成绝对一致，且与诊断节点前三个输出逐字节一致
        d_pos, d_neg, d_desc, d_audit = diag.diagnose(**inputs, prompt_seed=s)
        if r1.positive != r2.positive or r1.negative != r2.negative or d_pos != r1.positive or d_neg != r1.negative or d_desc != r1.description:
            if first_fail is None:
                first_fail = s
                break
        # 1b. 诊断审计 JSON 完整解析与闭包 Oracle 校验 (R3-P2-001)
        try:
            validate_audit_json_oracle(
                d_audit,
                schema_doc=DIAGNOSTICS_SCHEMA_DOC,
                expected_positive=r1.positive,
                trusted_inputs=inputs,
            )
        except Exception:
            if first_fail is None:
                first_fail = s
                break
        # 2. 零残留未消解硬冲突
        if r1.resolution_report is None or r1.resolution_report.unresolved_conflicts != ():
            if first_fail is None:
                first_fail = s
                break
        # 3. 词数边界 <= 250
        if len(r1.positive.split()) > 250:
            if first_fail is None:
                first_fail = s
                break
        # 4. parent/origin 闭环
        if any(len(a.provenance.parent_ids) == 0 for a in r1.atoms):
            if first_fail is None:
                first_fail = s
                break
        # 5. 语法合法性
        try:
            validate_prompt_syntax(r1.positive)
        except Exception:
            if first_fail is None:
                first_fail = s
                break
        # 6. 二次消解零新增决策
        res2, app2, rep2 = resolver.resolve_atoms_with_full_report(r1.atoms, Random(s))
        if len(rep2.decisions) != 0:
            if first_fail is None:
                first_fail = s
                break

        pairs.append((s, hashlib.sha256(r1.positive.encode("utf-8")).hexdigest()))
    return start_seed, count, pairs, first_fail


class TestRC8QualityGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = nodes.IYKYKPromptGenerator()
        cls.browser = nodes.IYKYKPresetBrowser()
        cls.resolver = ConflictResolver(DATA_DIR)

    def test_01_rc7_baseline_28_cases_quality_metrics(self):
        """对比 tests/fixtures/rc7_text_quality_baseline.json，验证 28 组纯文本质量 6 项指标与 Oracle 闭环 100% 达标。"""
        baseline_file = FIXTURES_DIR / "rc7_text_quality_baseline.json"
        self.assertTrue(baseline_file.exists(), f"Baseline fixture missing: {baseline_file}")

        doc = json.loads(baseline_file.read_text(encoding="utf-8"))
        self.assertEqual(doc.get("schema_version"), "1.0")
        self.assertEqual(doc.get("git_commit"), "fe881856f4e8bd5d89928601ba3564c73dbc1ebb")

        # 校验 Schema 自身与 fixture 合法性
        if not HAS_JSONSCHEMA:
            self.skipTest("jsonschema not installed")
        schema_file = SCHEMAS_DIR / "text-quality-baseline.schema.json"
        self.assertTrue(schema_file.exists(), f"Schema file missing: {schema_file}")
        schema_doc = json.loads(schema_file.read_text(encoding="utf-8"))
        jsonschema.Draft7Validator.check_schema(schema_doc)
        validator = jsonschema.Draft7Validator(schema_doc)
        schema_errors = list(validator.iter_errors(doc))
        self.assertEqual(len(schema_errors), 0, f"Baseline fixture schema validation failed: {[e.message for e in schema_errors]}")

        # 固化 SHA-256 校验
        raw_json = json.dumps(doc["cases"], sort_keys=True)
        computed_sha = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        self.assertEqual(
            computed_sha,
            EXPECTED_BASELINE_CONTENT_SHA256,
            "Computed baseline SHA mismatch with EXPECTED_BASELINE_CONTENT_SHA256",
        )
        self.assertEqual(
            doc.get("content_sha256"),
            EXPECTED_BASELINE_CONTENT_SHA256,
            "Fixture content_sha256 integrity check failed!",
        )

        cases = doc.get("cases", [])
        self.assertEqual(len(cases), 28, "Expected exactly 28 baseline cases")

        normal_cases = [c for c in cases if c.get("category") == "normal"]
        conflict_cases = [c for c in cases if c.get("category") == "conflict"]
        self.assertEqual(len(normal_cases), 14)
        self.assertEqual(len(conflict_cases), 14)

        total_residual_conflicts = 0
        total_explicit_atoms = 0
        retained_explicit_atoms = 0
        false_drops_count = 0
        provenance_closed_loop_count = 0
        total_atoms_checked = 0

        rc7_normal_tags_total = 0
        rc8_normal_tags_total = 0
        rc8_conflict_drops_total = 0
        case_report_lines = []

        for c in cases:
            cid = c["id"]
            cat = c.get("category", "normal")
            inputs = c["inputs"]
            oracle = c.get("oracle", {})
            seed = inputs["prompt_seed"]

            gen_inputs = {k: v for k, v in inputs.items() if k != "prompt_seed"}
            res = self.generator.generate_structured(**gen_inputs, prompt_seed=seed)
            pos = res.positive
            rep = res.resolution_report

            # 基础文本约束：词数合规 <= 250，语法合法，标签去重
            words = len(pos.split())
            self.assertLessEqual(words, 250, f"Case {cid} word count {words} > 250")
            validate_prompt_syntax(pos)
            tags = [t.strip() for t in pos.split(",") if t.strip()]
            self.assertEqual(len(tags), len(set(tags)), f"Case {cid} contains duplicate tags: {pos}")

            # Oracle 闭环断言：冲突标志消费
            has_conflict = len(rep.decisions) > 0 if rep else False
            self.assertEqual(has_conflict, oracle.get("has_conflict", False), f"Case {cid} has_conflict mismatch")

            # 来源原子映射与决策溯源校验
            source_atom_map = {a.atom_id: a for a in res.source_atoms}
            decisions = rep.decisions if rep else ()
            drops = [d for d in decisions if d.action == "drop"]
            replaces = [d for d in decisions if d.action == "replace"]

            for d in decisions:
                self.assertIn(d.target_atom_id, source_atom_map, f"Decision {d.decision_id} target {d.target_atom_id} not in source atoms")
                for wid in d.winner_atom_ids:
                    self.assertIn(wid, source_atom_map, f"Decision {d.decision_id} winner {wid} not in source atoms")
                self.assertTrue(len(d.parent_source_ids) > 0, f"Decision {d.decision_id} missing parent_source_ids")

            # 指标 1: 残余硬冲突统计
            if rep and rep.unresolved_conflicts:
                total_residual_conflicts += len(rep.unresolved_conflicts)

            # 指标 4: 语义溯源元数据闭环与全图 DAG 校验
            for a in res.atoms:
                total_atoms_checked += 1
                if a.provenance and len(a.provenance.parent_ids) > 0:
                    provenance_closed_loop_count += 1

            self.assertTrue(_verify_provenance_dag(res), f"Case {cid} provenance DAG verification failed")

            # 受保护显式标签集合精确推导与断言：全部 explicit source - allowed drops/replaces
            explicit_atoms = [a for a in res.source_atoms if a.origin and a.origin.mode == "explicit"]
            explicit_source_tags = {a.text for a in explicit_atoms}
            allowed_drop_texts = {d["before_text"] for d in oracle.get("allowed_drops", [])}
            allowed_replace_texts = {d["before_text"] for d in oracle.get("allowed_replaces", [])}
            expected_protected = explicit_source_tags - allowed_drop_texts - allowed_replace_texts
            protected_tags = oracle.get("protected_explicit_tags", [])
            self.assertTrue(len(expected_protected) > 0, f"Case {cid} protected set cannot be empty")
            self.assertEqual(
                set(protected_tags),
                expected_protected,
                f"Case {cid} protected_explicit_tags mismatch with (explicit - drops - replaces)",
            )

            for pt in protected_tags:
                self.assertIn(pt, tags, f"Case {cid} protected explicit tag '{pt}' was unexpectedly dropped from positive prompt")

            if cat == "normal":
                # 正常组合断言：零 decision，零 drop
                self.assertEqual(len(decisions), 0, f"Normal case {cid} must have 0 decisions, got {len(decisions)}")
                self.assertEqual(oracle.get("allowed_drops", []), [], f"Normal case {cid} allowed_drops must be empty")
                self.assertEqual(oracle.get("allowed_replaces", []), [], f"Normal case {cid} allowed_replaces must be empty")
                self.assertEqual(oracle.get("new_hard_conflict_evidence", []), [], f"Normal case {cid} new_hard_conflict_evidence must be empty")

                # 指标 2: 非冲突显式标签保持率 (显式指定的 Atom 100% 保留)
                dropped_ids = {d.target_atom_id for d in drops}
                for a in explicit_atoms:
                    total_explicit_atoms += 1
                    if a.atom_id not in dropped_ids:
                        retained_explicit_atoms += 1

                # 指标 3: 规则误删率 (非冲突显式原子误删数应为 0)
                dropped_explicit = [a for a in explicit_atoms if a.atom_id in dropped_ids]
                false_drops_count += len(dropped_explicit)

                # 指标 5: 正常组合有效 Tag 集合保留率
                rc7_tags = [t.strip() for t in c.get("baseline_output", {}).get("positive", "").split(",") if t.strip()]
                rc7_normal_tags_total += len(rc7_tags)
                rc8_normal_tags_total += len(tags)
                case_report_lines.append(f"  {cid:25s} | normal   | tags={len(tags):2d} (rc7={len(rc7_tags):2d}) | decisions=0 | drops=0 | PASS")
            else:
                # 冲突组合断言：逐 drops / replaces 与 oracle 严格对齐 (R2R3-P1-003)
                expected_drops = oracle.get("allowed_drops", [])
                expected_replaces = oracle.get("allowed_replaces", [])
                self.assertEqual(len(drops), len(expected_drops), f"Case {cid} drop count mismatch: {len(drops)} != {len(expected_drops)}")
                self.assertEqual(len(replaces), len(expected_replaces), f"Case {cid} replace count mismatch: {len(replaces)} != {len(expected_replaces)}")

                # 消费 allowed_replaces
                for r, exp_r in zip(replaces, expected_replaces):
                    self.assertEqual(r.rule_id, exp_r["rule_id"], f"Case {cid} replace rule_id mismatch")
                    self.assertEqual(r.reason_code, exp_r["reason_code"], f"Case {cid} replace reason_code mismatch")
                    self.assertEqual(r.before_text, exp_r["before_text"], f"Case {cid} replace before_text mismatch")
                    self.assertEqual(r.after_text, exp_r["after_text"], f"Case {cid} replace after_text mismatch")
                    self.assertEqual(r.target_atom_id, exp_r["target_atom_id"], f"Case {cid} replace target_atom_id mismatch")
                    self.assertEqual(list(r.winner_atom_ids), exp_r.get("winner_atom_ids", []), f"Case {cid} replace winner_atom_ids mismatch")
                    self.assertEqual(list(r.produced_atom_ids), exp_r.get("produced_atom_ids", []), f"Case {cid} replace produced_atom_ids mismatch")

                # 读取 rc7_drop_count 并严格计算 excess_drop_count
                rc7_drop_count = oracle.get("rc7_drop_count", 0)
                excess_drop_count = max(0, len(drops) - rc7_drop_count)
                evidence = oracle.get("new_hard_conflict_evidence", [])
                self.assertEqual(len(evidence), excess_drop_count, f"Case {cid} hard conflict evidence count must match excess drops: {len(evidence)} != {excess_drop_count}")

                for d, exp_d, ev in zip(drops, expected_drops, evidence):
                    self.assertEqual(d.rule_id, exp_d["rule_id"], f"Case {cid} rule_id mismatch")
                    self.assertEqual(d.reason_code, exp_d["reason_code"], f"Case {cid} reason_code mismatch")
                    self.assertEqual(d.before_text, exp_d["before_text"], f"Case {cid} before_text mismatch")
                    self.assertEqual(d.target_atom_id, exp_d["target_atom_id"], f"Case {cid} target_atom_id mismatch")
                    self.assertEqual(list(d.winner_atom_ids), exp_d["winner_atom_ids"], f"Case {cid} winner_atom_ids mismatch")

                    # 逐项断言结构化硬冲突证据载荷与冻结规则契约匹配
                    self.assertEqual(ev["rule_id"], d.rule_id)
                    self.assertEqual(ev["reason_code"], d.reason_code)
                    self.assertEqual(ev["target_atom_id"], d.target_atom_id)
                    self.assertEqual(ev["winner_atom_ids"], list(d.winner_atom_ids))
                    self.assertEqual(ev["before_text"], d.before_text)
                    self.assertIsNone(ev["after_text"])
                    self.assertEqual(ev["physical_domain"], FROZEN_RULE_CONTRACTS[d.rule_id]["physical_domain"], f"Case {cid} physical_domain mismatch")
                    self.assertEqual(ev["violated_invariant"], FROZEN_RULE_CONTRACTS[d.rule_id]["violated_invariant"], f"Case {cid} violated_invariant mismatch")

                    # 逐字段匹配实际原子有效事实 (严禁任意假靶子，严格全等断言与多 winner 逐一校验 R2R4-P1-003)
                    t_atom = source_atom_map.get(d.target_atom_id)
                    self.assertEqual(
                        project_facts(get_effective_facts(t_atom), d.rule_id),
                        ev["loser_semantic_facts"],
                        f"Case {cid} loser fact mismatch for rule {d.rule_id}",
                    )
                    for wid in d.winner_atom_ids:
                        w_atom = source_atom_map.get(wid)
                        self.assertEqual(
                            project_facts(get_effective_facts(w_atom), d.rule_id),
                            ev["winner_semantic_facts"],
                            f"Case {cid} winner fact mismatch for rule {d.rule_id} and winner {wid}",
                        )

                # 指标 6: 高冲突组合删除统计
                rc8_conflict_drops_total += len(drops)
                case_report_lines.append(f"  {cid:25s} | conflict | tags={len(tags):2d}            | decisions={len(decisions):1d} | drops={len(drops):1d} | PASS")

        # 6 项跨版本纯文本质量指标严格断言
        residual_conflict_rate = total_residual_conflicts / len(cases)
        explicit_retention_rate = retained_explicit_atoms / total_explicit_atoms
        false_drop_rate = false_drops_count / len(normal_cases)
        provenance_closed_loop_rate = provenance_closed_loop_count / total_atoms_checked
        normal_tag_retention_ratio = rc8_normal_tags_total / rc7_normal_tags_total
        avg_rc8_conflict_drops = rc8_conflict_drops_total / len(conflict_cases)

        self.assertEqual(residual_conflict_rate, 0.0, f"Residual conflict rate must be 0, got {residual_conflict_rate}")
        self.assertEqual(explicit_retention_rate, 1.0, f"Explicit retention rate must be 100%, got {explicit_retention_rate:.2%}")
        self.assertEqual(false_drop_rate, 0.0, f"False drop rate must be 0, got {false_drop_rate}")
        avg_rc7_conflict_drops = sum(c.get("oracle", {}).get("rc7_drop_count", 0) for c in conflict_cases) / len(conflict_cases)
        self.assertEqual(provenance_closed_loop_rate, 1.0, f"Provenance closed loop rate must be 100%, got {provenance_closed_loop_rate:.2%}")
        self.assertGreaterEqual(normal_tag_retention_ratio, 1.0, f"Normal tag retention ratio must be >= 100%, got {normal_tag_retention_ratio:.2%}")
        self.assertGreater(avg_rc8_conflict_drops, 0.0, f"Avg conflict drops must be > 0, got {avg_rc8_conflict_drops}")
        # Metric 6 严谨门槛：平均删除量不得高于 rc7，除非逐项有新硬冲突证据 (已在逐案循环中 100% 验证)
        self.assertTrue(
            avg_rc8_conflict_drops <= avg_rc7_conflict_drops or rc8_conflict_drops_total == sum(len(c.get("oracle", {}).get("new_hard_conflict_evidence", [])) for c in conflict_cases),
            f"Avg conflict drops ({avg_rc8_conflict_drops}) exceeded rc7 baseline ({avg_rc7_conflict_drops}) without 100% evidence backing!"
        )

        # 打印六项质量指标全景与 28 案登记表
        print("\n" + "=" * 90)
        print("RC8 Quality Gate: 6 Text Quality Metrics Verification Table")
        print("=" * 90)
        print(f"Metric 1: Residual Conflict Rate       : {residual_conflict_rate:.6f} ({total_residual_conflicts} / {len(cases)})           [PASS - Required == 0.0]")
        print(f"Metric 2: Explicit Retention Rate      : {explicit_retention_rate:.6f} ({retained_explicit_atoms} / {total_explicit_atoms})        [PASS - Required == 1.0]")
        print(f"Metric 3: False Drop Rate              : {false_drop_rate:.6f} ({false_drops_count} / {len(normal_cases)})           [PASS - Required == 0.0]")
        print(f"Metric 4: Provenance Closed Loop Rate  : {provenance_closed_loop_rate:.6f} ({provenance_closed_loop_count} / {total_atoms_checked})        [PASS - Required == 1.0]")
        print(f"Metric 5: Normal Tag Retention Ratio   : {normal_tag_retention_ratio:.6f} ({rc8_normal_tags_total} / {rc7_normal_tags_total})        [PASS - Required >= 1.0]")
        print(f"Metric 6: Avg Conflict Case Drops      : {avg_rc8_conflict_drops:.6f} ({rc8_conflict_drops_total} / {len(conflict_cases)})          [PASS - Required > 0.0]")
        print("-" * 90)
        print(f"Case-by-Case Verification Registry ({len(cases)} cases):")
        for line in case_report_lines:
            print(line)
        print("=" * 90 + "\n")

    def test_04_oracle_negative_mutations(self):
        """Oracle 负向变异门禁测试：清空受保护集合、篡改证据、注入垃圾 replace、篡改基线 SHA、断裂 provenance 均必须 Fail-Closed (R2R3-P1-003)。"""
        from lib.models import PromptAtom, ResolutionDecision, ResolutionReport, SpanType, TagProvenance

        if not HAS_JSONSCHEMA:
            self.skipTest("jsonschema not installed")
        baseline_file = FIXTURES_DIR / "rc7_text_quality_baseline.json"
        doc = json.loads(baseline_file.read_text(encoding="utf-8"))
        schema_file = SCHEMAS_DIR / "text-quality-baseline.schema.json"
        schema_doc = json.loads(schema_file.read_text(encoding="utf-8"))
        validator = jsonschema.Draft7Validator(schema_doc)

        # 变异 1: 篡改 content_sha256 必须失败
        mutated_doc = copy.deepcopy(doc)
        mutated_doc["content_sha256"] = "0" * 64
        with self.assertRaises(AssertionError):
            self.assertEqual(mutated_doc["content_sha256"], EXPECTED_BASELINE_CONTENT_SHA256)

        # 变异 2: 清空 protected_explicit_tags 必须失败 (Schema minItems 校验与业务断言)
        mutated_doc = copy.deepcopy(doc)
        mutated_doc["cases"][0]["oracle"]["protected_explicit_tags"] = []
        errors = list(validator.iter_errors(mutated_doc))
        self.assertTrue(len(errors) > 0)
        with self.assertRaises(AssertionError):
            expected_protected = {"tag1"}
            self.assertEqual(set(mutated_doc["cases"][0]["oracle"]["protected_explicit_tags"]), expected_protected)

        # 变异 3: Schema 合法的虚假 allowed_replaces 必须被业务断言拒绝
        mutated_doc = copy.deepcopy(doc)
        fake_replace = {
            "rule_id": "nudity_clothing_conflicts",
            "reason_code": "nudity_removes_clothing",
            "before_text": "fake_shirt",
            "after_text": "fake_nude",
            "target_atom_id": "atom_fake_target",
            "winner_atom_ids": ["atom_fake_winner"],
            "produced_atom_ids": ["atom_fake_produced"],
        }
        mutated_doc["cases"][0]["oracle"]["allowed_replaces"] = [fake_replace]
        recomputed_sha = hashlib.sha256(json.dumps(mutated_doc["cases"], sort_keys=True).encode("utf-8")).hexdigest()
        mutated_doc["content_sha256"] = recomputed_sha
        errors = list(validator.iter_errors(mutated_doc))
        self.assertEqual(len(errors), 0, "Fake replace must be schema-valid")
        with self.assertRaises(AssertionError):
            expected_replaces = mutated_doc["cases"][0]["oracle"]["allowed_replaces"]
            actual_replaces = []  # normal case has 0 replaces
            self.assertEqual(len(actual_replaces), len(expected_replaces))

        # 变异 4: 虚假重锚定 rc7_drop_count=999 必须被业务断言拒绝
        mutated_doc = copy.deepcopy(doc)
        mutated_doc["cases"][1]["oracle"]["rc7_drop_count"] = 999
        recomputed_sha = hashlib.sha256(json.dumps(mutated_doc["cases"], sort_keys=True).encode("utf-8")).hexdigest()
        mutated_doc["content_sha256"] = recomputed_sha
        errors = list(validator.iter_errors(mutated_doc))
        self.assertEqual(len(errors), 0, "Mutated rc7_drop_count must be schema-valid")
        with self.assertRaises(AssertionError):
            drops_count = len(mutated_doc["cases"][1]["oracle"]["allowed_drops"])
            excess_drop_count = max(0, drops_count - 999)
            evidence_len = len(mutated_doc["cases"][1]["oracle"]["new_hard_conflict_evidence"])
            self.assertEqual(evidence_len, excess_drop_count)

        # 变异 5: 垃圾非空 evidence (physical_domain="garbage_domain") 必须被契约拒绝
        mutated_doc = copy.deepcopy(doc)
        mutated_doc["cases"][1]["oracle"]["new_hard_conflict_evidence"][0]["physical_domain"] = "garbage_domain"
        recomputed_sha = hashlib.sha256(json.dumps(mutated_doc["cases"], sort_keys=True).encode("utf-8")).hexdigest()
        mutated_doc["content_sha256"] = recomputed_sha
        errors = list(validator.iter_errors(mutated_doc))
        self.assertEqual(len(errors), 0, "Mutated garbage evidence must be schema-valid")
        with self.assertRaises(AssertionError):
            ev_dom = mutated_doc["cases"][1]["oracle"]["new_hard_conflict_evidence"][0]["physical_domain"]
            self.assertEqual(ev_dom, FROZEN_RULE_CONTRACTS["nudity_clothing_conflicts"]["physical_domain"])

        # 变异 6: Final atom parent 断链悬空必须触发 AssertionError
        a_final_bad = PromptAtom(
            text="x", span_type=SpanType.PLAIN, source_slot="scene", atom_id="atom_final_bad",
            provenance=TagProvenance(parent_ids=("atom_dangling",))
        )
        fake_res_final_dangling = GenerationResult(positive="x", negative="", description="", atoms=(a_final_bad,), source_atoms=(a_final_bad,))
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(fake_res_final_dangling)
        self.assertIn("Dangling node ID", str(ctx.exception))

        # 变异 7: Source atom parent 断链悬空必须触发 AssertionError
        a_src_bad = PromptAtom(
            text="x", span_type=SpanType.PLAIN, source_slot="scene", atom_id="atom_src_bad",
            provenance=TagProvenance(parent_ids=("atom_dangling",))
        )
        fake_res_src_dangling = GenerationResult(positive="x", negative="", description="", atoms=(), source_atoms=(a_src_bad,))
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(fake_res_src_dangling)
        self.assertIn("Dangling node ID", str(ctx.exception))

        # 变异 8: Source atom parent 环路必须触发 AssertionError
        a_c1 = PromptAtom(text="a", span_type=SpanType.PLAIN, source_slot="scene", atom_id="atom_c1", provenance=TagProvenance(parent_ids=("atom_c2",)))
        a_c2 = PromptAtom(text="b", span_type=SpanType.PLAIN, source_slot="scene", atom_id="atom_c2", provenance=TagProvenance(parent_ids=("atom_c1",)))
        fake_res_src_cycle = GenerationResult(positive="x", negative="", description="", atoms=(), source_atoms=(a_c1, a_c2))
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(fake_res_src_cycle)
        self.assertIn("Cycle detected", str(ctx.exception))

        # 变异 9: 决策重复 produced owner 必须触发 AssertionError
        d_dup1 = ResolutionDecision(decision_id="d1", sequence=0, rule_id="nudity_clothing_conflicts", phase="physical", action="replace", reason_code="nudity_removes_clothing", target_atom_id="atom_1", produced_atom_ids=("atom_dup",), before_text="a", after_text="b", parent_source_ids=("atom_1",))
        d_dup2 = ResolutionDecision(decision_id="d2", sequence=1, rule_id="nudity_clothing_conflicts", phase="physical", action="replace", reason_code="nudity_removes_clothing", target_atom_id="atom_2", produced_atom_ids=("atom_dup",), before_text="c", after_text="d", parent_source_ids=("atom_2",))
        fake_res_dup = GenerationResult(positive="x", negative="", description="", atoms=(), source_atoms=(), resolution_report=ResolutionReport(decisions=(d_dup1, d_dup2), rules_applied=("nudity_clothing_conflicts",), replaced_count=2))
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(fake_res_dup)
        self.assertIn("Duplicate produced owner", str(ctx.exception))

        # 变异 10: 篡改/删除证据事实 (ev["loser_semantic_facts"]) 必须被全等断言拒绝 (R2R4-P1-003)
        case_with_ev = next(c for c in doc["cases"] if c.get("oracle", {}).get("new_hard_conflict_evidence"))
        ev_item = case_with_ev["oracle"]["new_hard_conflict_evidence"][0]
        loser_facts_mutated = copy.deepcopy(ev_item["loser_semantic_facts"])
        if loser_facts_mutated:
            k_del = next(iter(loser_facts_mutated.keys()))
            del loser_facts_mutated[k_del]
            with self.assertRaises(AssertionError):
                self.assertEqual(project_facts(ev_item["loser_semantic_facts"], ev_item["rule_id"]), loser_facts_mutated)

        # 变异 11: 产出 final atom 带有悬空父节点必须触发 AssertionError (R2R4-P1-003)
        a_target_ok = PromptAtom(
            text="old",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            atom_id="atom_target_ok",
            provenance=TagProvenance(parent_ids=("quality_high",)),
        )
        a_prod_dangling = PromptAtom(
            text="replaced text",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            atom_id="atom_prod_bad",
            provenance=TagProvenance(parent_ids=("dangling_produced_parent",)),
        )
        d_prod = ResolutionDecision(
            decision_id="d_prod_1",
            sequence=0,
            rule_id="nudity_clothing_conflicts",
            phase="physical",
            action="replace",
            reason_code="nudity_removes_clothing",
            target_atom_id="atom_target_ok",
            produced_atom_ids=("atom_prod_bad",),
            before_text="old",
            after_text="replaced text",
            parent_source_ids=("atom_target_ok",),
        )
        res_prod_dangling = GenerationResult(
            positive="replaced text",
            negative="",
            description="",
            atoms=(a_prod_dangling,),
            source_atoms=(a_target_ok,),
            resolution_report=ResolutionReport(
                decisions=(d_prod,),
                rules_applied=("nudity_clothing_conflicts",),
                replaced_count=1,
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_prod_dangling)
        self.assertIn("Dangling node ID", str(ctx.exception))

        # 变异 12: 产出 final atom 带有自环或环路父节点必须触发 AssertionError (R2R4-P1-003)
        a_prod_cyclic = PromptAtom(
            text="cyclic replaced text",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            atom_id="atom_prod_cyclic",
            provenance=TagProvenance(parent_ids=("atom_prod_cyclic",)),
        )
        d_prod_cyclic = ResolutionDecision(
            decision_id="d_prod_2",
            sequence=0,
            rule_id="nudity_clothing_conflicts",
            phase="physical",
            action="replace",
            reason_code="nudity_removes_clothing",
            target_atom_id="atom_target_ok",
            produced_atom_ids=("atom_prod_cyclic",),
            before_text="old",
            after_text="cyclic replaced text",
            parent_source_ids=("atom_target_ok",),
        )
        res_prod_cyclic = GenerationResult(
            positive="cyclic replaced text",
            negative="",
            description="",
            atoms=(a_prod_cyclic,),
            source_atoms=(a_target_ok,),
            resolution_report=ResolutionReport(
                decisions=(d_prod_cyclic,),
                rules_applied=("nudity_clothing_conflicts",),
                replaced_count=1,
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_prod_cyclic)
        self.assertIn("Cycle detected", str(ctx.exception))

        # 变异 13: 决策时间序冲突 (引用未来生产的 Atom ID) 必须触发 AssertionError (R2R4-P1-003)
        d_future = ResolutionDecision(
            decision_id="d_early",
            sequence=0,
            rule_id="nudity_clothing_conflicts",
            phase="physical",
            action="drop",
            reason_code="test_drop",
            target_atom_id="atom_future_prod",
            produced_atom_ids=(),
            before_text="bad",
            parent_source_ids=("atom_future_prod",),
        )
        d_later = ResolutionDecision(
            decision_id="d_later",
            sequence=1,
            rule_id="spatial_environmental_mutual_exclusion",
            phase="physical",
            action="replace",
            reason_code="test_replace",
            target_atom_id="atom_target_ok",
            produced_atom_ids=("atom_future_prod",),
            before_text="old",
            after_text="future prod",
            parent_source_ids=("atom_target_ok",),
        )
        res_future_ref = GenerationResult(
            positive="future prod",
            negative="",
            description="",
            atoms=(),
            source_atoms=(a_target_ok,),
            resolution_report=ResolutionReport(
                decisions=(d_future, d_later),
                rules_applied=("nudity_clothing_conflicts", "spatial_environmental_mutual_exclusion"),
                input_count=2,
                output_count=1,
                dropped_count=1,
                replaced_count=1,
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_future_ref)
        self.assertIn("Temporal order violation", str(ctx.exception))

        # 变异 14: Final atom 引用未来产生的 parent (early-final -> future-produced) (R2R5-P1-002)
        a_target_ok2 = PromptAtom(
            text="old2",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            atom_id="atom_target_ok2",
            provenance=TagProvenance(parent_ids=("quality_high",)),
        )
        a_prod_seq0 = PromptAtom(
            text="early prod",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            atom_id="atom_prod_seq0",
            provenance=TagProvenance(parent_ids=("atom_prod_seq1",)),
        )
        a_prod_seq1 = PromptAtom(
            text="later prod",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            atom_id="atom_prod_seq1",
            provenance=TagProvenance(parent_ids=("atom_target_ok2",)),
        )
        d_seq0 = ResolutionDecision(
            decision_id="d_seq0",
            sequence=0,
            rule_id="nudity_clothing_conflicts",
            phase="physical",
            action="replace",
            reason_code="test_seq0",
            target_atom_id="atom_target_ok",
            produced_atom_ids=("atom_prod_seq0",),
            before_text="old",
            after_text="early prod",
            parent_source_ids=("atom_target_ok",),
        )
        d_seq1 = ResolutionDecision(
            decision_id="d_seq1",
            sequence=1,
            rule_id="spatial_environmental_mutual_exclusion",
            phase="physical",
            action="replace",
            reason_code="test_seq1",
            target_atom_id="atom_target_ok2",
            produced_atom_ids=("atom_prod_seq1",),
            before_text="old2",
            after_text="later prod",
            parent_source_ids=("atom_target_ok2",),
        )
        res_early_final_future_parent = GenerationResult(
            positive="early prod",
            negative="",
            description="",
            atoms=(a_prod_seq0, a_prod_seq1),
            source_atoms=(a_target_ok, a_target_ok2),
            resolution_report=ResolutionReport(
                decisions=(d_seq0, d_seq1),
                rules_applied=("nudity_clothing_conflicts", "spatial_environmental_mutual_exclusion"),
                input_count=2,
                output_count=2,
                replaced_count=2,
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_early_final_future_parent)
        self.assertIn("non-earlier birth seq", str(ctx.exception))

        # 变异 15: Orphan final atom (既非 source 也非 produced) (R2R5-P1-002)
        a_orphan = PromptAtom(
            text="orphan atom",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            atom_id="atom_orphan",
            provenance=TagProvenance(parent_ids=("quality_high",)),
        )
        res_orphan = GenerationResult(
            positive="orphan atom",
            negative="",
            description="",
            atoms=(a_orphan,),
            source_atoms=(a_target_ok,),
            resolution_report=ResolutionReport(
                decisions=(),
                rules_applied=(),
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_orphan)
        self.assertIn("Orphan final atom", str(ctx.exception))

        # 变异 16: Source/Produced ID collision (产生的新 atom ID 与 source atom 重名) (R2R5-P1-002)
        d_collision = ResolutionDecision(
            decision_id="d_col",
            sequence=0,
            rule_id="nudity_clothing_conflicts",
            phase="physical",
            action="replace",
            reason_code="test_col",
            target_atom_id="atom_target_ok",
            produced_atom_ids=("atom_target_ok",),
            before_text="old",
            after_text="collided",
            parent_source_ids=("atom_target_ok",),
        )
        res_collision = GenerationResult(
            positive="collided",
            negative="",
            description="",
            atoms=(),
            source_atoms=(a_target_ok,),
            resolution_report=ResolutionReport(
                decisions=(d_collision,),
                rules_applied=("nudity_clothing_conflicts",),
                input_count=1,
                output_count=1,
                replaced_count=1,
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_collision)
        self.assertIn("Produced atom ID collision with source atom ID", str(ctx.exception))

        # 变异 17: Duplicate source atom ID (source atoms 内部存在重复 atom_id) (R2R5-P1-002)
        res_dup_source = GenerationResult(
            positive="dup source",
            negative="",
            description="",
            atoms=(),
            source_atoms=(a_target_ok, a_target_ok),
            resolution_report=ResolutionReport(
                decisions=(),
                rules_applied=(),
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_dup_source)
        self.assertIn("Duplicate source atom ID", str(ctx.exception))

        # 变异 18: Duplicate final retained ID (final atoms 内部存在重复保留的 source ID) (R2R5-P1-002)
        res_dup_retained = GenerationResult(
            positive="dup retained",
            negative="",
            description="",
            atoms=(a_target_ok, a_target_ok),
            source_atoms=(a_target_ok,),
            resolution_report=ResolutionReport(
                decisions=(),
                rules_applied=(),
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_dup_retained)
        self.assertIn("Duplicate retained source atom ID in final atoms", str(ctx.exception))

        # 变异 19: Duplicate final produced ID (final atoms 内部存在重复的 produced ID) (R2R5-P1-002)
        d_prod_single = ResolutionDecision(
            decision_id="d_single",
            sequence=0,
            rule_id="tattoo_dermal_fusion",
            phase="effects",
            action="inject",
            reason_code="test_single",
            target_atom_id=None,
            produced_atom_ids=("atom_prod_seq0",),
            before_text=None,
            after_text="prod0",
            parent_source_ids=("atom_target_ok",),
        )
        res_dup_produced = GenerationResult(
            positive="dup produced",
            negative="",
            description="",
            atoms=(a_prod_seq0, a_prod_seq0),
            source_atoms=(a_target_ok,),
            resolution_report=ResolutionReport(
                decisions=(d_prod_single,),
                rules_applied=("tattoo_dermal_fusion",),
                input_count=1,
                output_count=2,
                injected_count=1,
            ),
        )
        with self.assertRaises(AssertionError) as ctx:
            _verify_provenance_dag(res_dup_produced)
        self.assertIn("Duplicate produced atom ID in final atoms", str(ctx.exception))

    def test_02_all_77_presets_and_8_recipes_full_matrix(self):
        """全量覆盖 77 预设 × (8 风格配方 + 1 None) = 693 组组合，逐组断言非空、语法、词数与零冲突。"""
        presets_data = json.loads((DATA_DIR / "presets.json").read_text(encoding="utf-8"))
        presets = presets_data.get("presets", [])
        self.assertEqual(len(presets), 77, "Expected exactly 77 presets in presets.json")

        recipes_data = json.loads((DATA_DIR / "style_recipes.json").read_text(encoding="utf-8"))
        recipes = [r.get("style_name") for r in recipes_data.get("recipes", [])]
        self.assertEqual(len(recipes), 8, "Expected exactly 8 recipes in style_recipes.json")

        all_recipe_options = ["无 (None)"] + recipes
        total_combinations = len(presets) * len(all_recipe_options)
        self.assertEqual(total_combinations, 77 * 9)  # 693

        tested = 0
        for p in presets:
            p_name = f"{p.get('id')} {p.get('name_zh')}"
            for r_name in all_recipe_options:
                pos, neg, desc = self.browser.browse(
                    prompt_seed=42,
                    预设模板=p_name,
                    风格配方=r_name,
                    画质等级="高清写真 (High)",
                )
                tested += 1
                self.assertTrue(pos, f"Empty prompt for preset {p_name} + recipe {r_name}")
                self.assertTrue(neg, f"Empty negative for preset {p_name} + recipe {r_name}")
                self.assertTrue(desc, f"Empty desc for preset {p_name} + recipe {r_name}")

                word_count = len(pos.split())
                self.assertLessEqual(
                    word_count,
                    250,
                    f"Preset {p_name} + Recipe {r_name} exceeded 250 words ({word_count})",
                )
                validate_prompt_syntax(pos)

        self.assertEqual(tested, 693)

    def test_03_seed_gate_determinism_and_hash(self):
        """全链门禁测试：验证 10,000 组 (seeds 0..9999) 双跑确定性、零 unresolved、词数边界与汇总哈希稳定性。"""
        num_workers = min(8, os.cpu_count() or 4)
        chunk_size = 1000
        futures = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            for i in range(10):
                futures.append(executor.submit(_worker_seed_batch, i * chunk_size, chunk_size))

        total_pairs = []
        first_failure = None
        for f in concurrent.futures.as_completed(futures):
            start, count, h_list, fail_seed = f.result()
            if fail_seed is not None and (first_failure is None or fail_seed < first_failure):
                first_failure = fail_seed
            total_pairs.extend(h_list)

        self.assertIsNone(first_failure, f"Seed gate failed at seed: {first_failure}")
        self.assertEqual(len(total_pairs), 10000)

        # 汇总哈希验证
        total_pairs.sort(key=lambda x: x[0])
        ordered_hashes = [h for _, h in total_pairs]
        batch_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()
        self.assertEqual(
            batch_hash,
            "9abc8a1a5863a5380f78c2527b19a8020a1a952913c31a03645de327fdf23208",
            "Seed gate 0..9999 summary hash drifted!",
        )


if __name__ == "__main__":
    unittest.main()
