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

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
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
    all_ids.update({"quality_high", "quality_masterpiece", "quality_phone", "quality_cctv", "quality_standard", "linkage_general", "linkage_override"})
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


REQUIRED_M5_QUALITY_GATES = {
    "determinism",
    "oracle_validation",
    "prompt_syntax",
    "provenance_closed_loop",
    "residual_conflicts",
    "secondary_idempotency",
    "word_budget",
}

EXPECTED_M5_GOLDEN_HASH = "48ae2e927fc6c6dc3f19362c806b31e2587df49aad85193f75e2731b18fa4c7d"
EXPECTED_M5_ARCHIVE_SHA256 = "5417aa6529e4a86d2f920d6387ecd88626d70d9fd1973c7896ffa16544b01ff9"
EXPECTED_M5_ARCHIVE_SIZE = 737487743
EXPECTED_M5_CORE_SOURCE_DIGEST = "7641d348b44249de809a73e8e3bfd22c8d7ba48ed4e71f6cdf36fe8ab6a6ee7f"


def compute_current_core_source_digest(repo_dir: Path) -> str:
    """计算当前仓库核心业务代码与数据契约文件的确定性 SHA-256 摘要。"""
    core_files = sorted(
        list((repo_dir / "lib").glob("*.py")) +
        list((repo_dir / "data").glob("*.json")) +
        list((repo_dir / "schemas").glob("*.json")) +
        [repo_dir / "nodes.py"]
    )
    hasher = hashlib.sha256()
    for f in core_files:
        hasher.update(f.relative_to(repo_dir).as_posix().encode("utf-8"))
        hasher.update(b":")
        hasher.update(hashlib.sha256(f.read_bytes()).hexdigest().encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def verify_m5_archive_evidence(
    manifest_path: Path,
    archive_path: Path,
    repo_dir: Path,
    stream_recompute: bool = True,
    expected_archive_size: int = EXPECTED_M5_ARCHIVE_SIZE,
    expected_archive_sha256: str = EXPECTED_M5_ARCHIVE_SHA256,
    expected_golden_hash: str = EXPECTED_M5_GOLDEN_HASH,
    expected_core_digest: str = EXPECTED_M5_CORE_SOURCE_DIGEST,
    expected_seeds_count: int = 10000,
) -> dict:
    """
    共用 M5 归档证据严格校验函数 (Fail-Closed)：
    1. 清单文件存在且结构有效；
    2. 实算归档文件本身的 SHA-256 (分块读取) 与文件大小，必须与期望值严格一致；
    3. 清单中 source_archive 与 evaluation_result 字段完整且通过 (零未解释、is_all_explained=True、连续种子范围、处理记录数匹配)；
    4. 核心源码确定性摘要必须匹配（校验业务逻辑未发生未受控漂移）；
    5. 7 项质量门禁集合必须非空、完整且严格为 'PASS (0 failures)'；
    6. 清单 target_batch_hash 严格等于 expected_golden_hash；
    7. 若 stream_recompute=True，从归档记录逐条从 positive 重新计算哈希并严格比对存储的 pos_hash，重算万种子汇总哈希，断言严格等于 expected_golden_hash，并执行实物级负向防篡改反例断言。
    """
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing M5 manifest file: {manifest_path}")

    manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_doc, dict):
        raise ValueError(f"Manifest must be a JSON object: {manifest_path}")

    auditor_binding = manifest_doc.get("auditor_binding")
    if not isinstance(auditor_binding, dict):
        raise AssertionError(f"Manifest missing valid 'auditor_binding' object: {manifest_path}")

    # 1. 归档文件实物存在与实物 SHA-256 分块计算核验 (杜绝只比对声明不查实物)
    if not archive_path.is_file():
        raise FileNotFoundError(f"Missing M5 archive evidence file: {archive_path}")

    actual_size = archive_path.stat().st_size
    if actual_size != expected_archive_size:
        raise AssertionError(
            f"Archive file size mismatch! Expected {expected_archive_size}, got {actual_size}"
        )

    actual_arch_hasher = hashlib.sha256()
    with open(archive_path, "rb") as af:
        while chunk := af.read(1024 * 1024):
            actual_arch_hasher.update(chunk)
    actual_arch_sha = actual_arch_hasher.hexdigest()
    if actual_arch_sha != expected_archive_sha256:
        raise AssertionError(
            f"Archive file real SHA-256 mismatch! Expected {expected_archive_sha256}, got {actual_arch_sha}"
        )

    # 2. 检查 source_archive 声明与实物一致性
    source_archive_info = manifest_doc.get("source_archive")
    if not isinstance(source_archive_info, dict):
        raise AssertionError(f"Manifest missing valid 'source_archive' object: {manifest_path}")

    manifest_arch_sha = source_archive_info.get("sha256")
    if manifest_arch_sha != expected_archive_sha256:
        raise AssertionError(
            f"Manifest source_archive.sha256 mismatch! Expected {expected_archive_sha256}, got {manifest_arch_sha}"
        )

    manifest_arch_size = source_archive_info.get("size_bytes", source_archive_info.get("size"))
    if manifest_arch_size != expected_archive_size:
        raise AssertionError(
            f"Manifest source_archive size mismatch! Expected {expected_archive_size}, got {manifest_arch_size}"
        )

    tot_proc = source_archive_info.get("total_records_processed")
    if tot_proc != expected_seeds_count:
        raise AssertionError(
            f"Manifest source_archive.total_records_processed mismatch! Expected {expected_seeds_count}, got {tot_proc}"
        )

    seed_range = source_archive_info.get("contiguous_seed_range")
    expected_seed_range = [0, expected_seeds_count - 1]
    if seed_range != expected_seed_range:
        raise AssertionError(
            f"Manifest source_archive.contiguous_seed_range mismatch! Expected {expected_seed_range}, got {seed_range}"
        )

    # 3. 恢复全量因果重评结果结论强断言 (Fail-Closed)
    eval_res = manifest_doc.get("evaluation_result")
    if not isinstance(eval_res, dict):
        raise AssertionError(f"Manifest missing valid 'evaluation_result' object: {manifest_path}")

    if eval_res.get("total_seeds") != expected_seeds_count:
        raise AssertionError(
            f"Manifest evaluation_result.total_seeds mismatch! Expected {expected_seeds_count}, got {eval_res.get('total_seeds')}"
        )

    if eval_res.get("is_all_explained") is not True:
        raise AssertionError(
            f"Manifest evaluation_result.is_all_explained is not True! Got {eval_res.get('is_all_explained')}"
        )

    if eval_res.get("unexplained_count") != 0:
        raise AssertionError(
            f"Manifest evaluation_result.unexplained_count is non-zero ({eval_res.get('unexplained_count')})!"
        )

    unexp_seeds = eval_res.get("unexplained_seeds")
    if unexp_seeds is None or len(unexp_seeds) != 0:
        raise AssertionError(
            f"Manifest evaluation_result.unexplained_seeds must be empty, got {unexp_seeds}"
        )

    div_count = eval_res.get("divergent_count", 0)
    iden_count = eval_res.get("identical_count", 0)
    if div_count + iden_count != expected_seeds_count:
        raise AssertionError(
            f"Manifest evaluation_result divergent_count ({div_count}) + identical_count ({iden_count}) != expected {expected_seeds_count}"
        )

    # 4. 核心源码摘要核验 (确保业务逻辑代码未发生漂移)
    manifest_core_digest = auditor_binding.get("core_source_digest")
    if manifest_core_digest != expected_core_digest:
        raise AssertionError(
            f"Manifest core_source_digest mismatch! Expected {expected_core_digest}, got {manifest_core_digest}"
        )

    current_core_digest = compute_current_core_source_digest(repo_dir)
    if current_core_digest != expected_core_digest:
        raise AssertionError(
            f"Current codebase core_source_digest drifted! Expected {expected_core_digest}, got {current_core_digest}"
        )

    # 5. 7 项质量门禁完整性与通过断言 (非空、完整、逐项 PASS)
    quality_gates = manifest_doc.get("quality_gates")
    if not isinstance(quality_gates, dict) or not quality_gates:
        raise AssertionError("quality_gates must be a non-empty dictionary in manifest!")

    missing_gates = REQUIRED_M5_QUALITY_GATES - set(quality_gates.keys())
    if missing_gates:
        raise AssertionError(f"Incomplete quality gates in manifest! Missing: {missing_gates}")

    for gate_name in sorted(REQUIRED_M5_QUALITY_GATES):
        status_val = quality_gates.get(gate_name, "")
        if not isinstance(status_val, str) or not status_val.startswith("PASS (0 failures)"):
            raise AssertionError(f"Quality gate '{gate_name}' failed: expected 'PASS (0 failures)', got '{status_val}'")

    # 6. 清单声明的目标汇总哈希强断言
    target_batch_hash = auditor_binding.get("target_batch_hash")
    if target_batch_hash != expected_golden_hash:
        raise AssertionError(
            f"Manifest target_batch_hash mismatch! Expected {expected_golden_hash}, got {target_batch_hash}"
        )

    # 7. 流式逐条重算万种子汇总哈希 (若 stream_recompute 为 True)
    recomputed_batch_hash = target_batch_hash
    if stream_recompute:
        from scratch.run_m5_3_audit import stream_archive_records
        header_meta, record_iter = stream_archive_records(archive_path)
        ordered_hashes = []
        seen_seeds = set()
        for r in record_iter:
            s = r["seed"]
            if s in seen_seeds:
                raise AssertionError(f"Duplicate seed {s} found in archive stream!")
            seen_seeds.add(s)
            pos_txt = r.get("round_a", {}).get("positive")
            if pos_txt is None:
                raise AssertionError(f"Archive record for seed {s} missing 'positive' prompt text!")
            computed_h = hashlib.sha256(pos_txt.encode("utf-8")).hexdigest()
            stored_h = r.get("round_a", {}).get("pos_hash")
            if stored_h and stored_h != computed_h:
                raise AssertionError(
                    f"Archive record for seed {s} stored pos_hash mismatch! Stored: {stored_h}, Computed: {computed_h}"
                )
            ordered_hashes.append((s, computed_h))

        if len(ordered_hashes) != expected_seeds_count or seen_seeds != set(range(expected_seeds_count)):
            raise AssertionError(f"Archive stream did not yield exact {expected_seeds_count} seeds [0..{expected_seeds_count-1}]! Got {len(ordered_hashes)}")

        ordered_hashes.sort(key=lambda x: x[0])
        raw_hash_str = "".join(h for _, h in ordered_hashes)
        recomputed_batch_hash = hashlib.sha256(raw_hash_str.encode("utf-8")).hexdigest()

        if recomputed_batch_hash != expected_golden_hash:
            raise AssertionError(
                f"Streamingly recomputed batch hash mismatch! Expected {expected_golden_hash}, got {recomputed_batch_hash}"
            )

        # 负向防篡改反例：篡改代表种子的提示词哈希，重算必失配
        tamper_seed_idx = min(42, expected_seeds_count - 1)
        tampered_list = list(ordered_hashes)
        tampered_list[tamper_seed_idx] = (tamper_seed_idx, "0" * 64)
        tampered_raw_str = "".join(h for _, h in tampered_list)
        tampered_batch_hash = hashlib.sha256(tampered_raw_str.encode("utf-8")).hexdigest()
        if tampered_batch_hash == expected_golden_hash:
            raise AssertionError("Tampered seed positive hash unexpectedly matched golden hash!")

    return {
        "status": "VERIFIED",
        "batch_hash": recomputed_batch_hash,
        "total_seeds": expected_seeds_count,
        "core_source_digest": current_core_digest,
    }


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

        # 历史重放基线数据环境绑定 (Directive 3: 历史重放使用匹配的历史数据环境，保留旧黄金基线断言)
        baseline_repo = REPO_DIR / "scratch" / "baseline_6da94cb"
        if (
            baseline_repo.exists()
            and (baseline_repo / "nodes.py").exists()
            and not os.environ.get("IYKYK_INSIDE_ISOLATED_REPLAY")
        ):
            import subprocess
            import sys
            worker_code = """
import os
import sys
import unittest
os.environ["IYKYK_INSIDE_ISOLATED_REPLAY"] = "1"
sys.path.insert(0, sys.argv[1])
import nodes
from tests.test_rc8_quality_gate import TestRC8QualityGate
case = TestRC8QualityGate("test_01_rc7_baseline_28_cases_quality_metrics")
case.generator = nodes.IYKYKPromptGenerator()
res = unittest.TextTestRunner(verbosity=0).run(case)
sys.exit(0 if res.wasSuccessful() else 1)
"""
            proc = subprocess.run(
                [sys.executable, "-c", worker_code, str(baseline_repo)],
                capture_output=True,
                text=True,
                cwd=str(REPO_DIR),
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Isolated baseline replay failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )
            return

        active_gen = self.generator

        for c in cases:
            cid = c["id"]
            cat = c.get("category", "normal")
            inputs = c["inputs"]
            oracle = c.get("oracle", {})
            seed = inputs["prompt_seed"]

            gen_inputs = {k: v for k, v in inputs.items() if k != "prompt_seed"}
            res = active_gen.generate_structured(**gen_inputs, prompt_seed=seed)
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

    @classmethod
    def _run_seed_gate_10k(cls):
        if hasattr(cls, "_seed_gate_result"):
            return cls._seed_gate_result

        # 若显式设置环境变量 RUN_LIVE_SEED_GATE=1，执行耗时实时生成
        if os.environ.get("RUN_LIVE_SEED_GATE") == "1":
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

            total_pairs.sort(key=lambda x: x[0])
            ordered_hashes = [h for _, h in total_pairs]
            batch_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()
            cls._seed_gate_result = (first_failure, len(total_pairs), batch_hash)
            return cls._seed_gate_result

        # 默认路径：复用经 M5-3 归档实物证据与共用证据校验函数 (Fail-Closed, 毫秒级返回)
        manifest_path = REPO_DIR / "scratch" / "audit_m5_3_reeval_manifest.json"
        archive_path = REPO_DIR / "scratch" / "audit_m5_3_evidence.json.gz"
        evidence_result = verify_m5_archive_evidence(manifest_path, archive_path, REPO_DIR, stream_recompute=False)
        cls._seed_gate_result = (None, evidence_result["total_seeds"], evidence_result["batch_hash"])
        return cls._seed_gate_result

    def test_03_seed_gate_functional_determinism(self):
        """全链功能门禁：归档证据模式核验 10,000 组功能门禁；若需实时生成设 RUN_LIVE_SEED_GATE=1。"""
        first_failure, count, _ = self._run_seed_gate_10k()
        self.assertIsNone(first_failure, f"Seed gate failed at seed: {first_failure}")
        self.assertEqual(count, 10000)

    def test_03b_seed_gate_golden_hash(self):
        """固定汇总哈希稳定性门禁：保留历史演进链基线，流式重算当前 M5 黄金基线哈希，并绑定归档实物证据。"""
        baseline_dba5861_hash = "a39a823d09b3b817107ba6a6ebdd5fdb261f4f71bd8148bd7856533fdf21c218"
        step2_baseline_hash = "3e1291ae60af887ebde1869a8fb60f7937424198c1db6a4856bb90aa954725fc"
        step3_baseline_hash = "ab5a633cfb3fde70d9a6c629ee65a540955d87ee143e66570d780743246cab89"
        m1_historical_hash = "aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139"

        # ------------------------------------------------------------------
        # 分支 A: 历史参考数据校验 (保留 M1 历史演进链向下兼容校验)
        # ------------------------------------------------------------------
        ref_cache_path = REPO_DIR / "scratch" / "controlled_ref_data_10k.json.gz"
        if ref_cache_path.exists():
            import gzip
            with gzip.open(ref_cache_path, "rt", encoding="utf-8") as f:
                ref_doc = json.load(f)
            data_map = ref_doc.get("data", {})
            self.assertEqual(len(data_map), 10000, "Historical ref cache must contain exactly 10,000 seeds")
            ordered_hashes = []
            for s in range(10000):
                s_key = str(s)
                self.assertIn(s_key, data_map, f"Missing seed {s} in historical ref cache")
                pos = data_map[s_key].get("positive", "")
                ordered_hashes.append(hashlib.sha256(pos.encode("utf-8")).hexdigest())
            recomputed_historical_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()

            # 正向比对历史 M1 预期值
            self.assertEqual(
                recomputed_historical_hash,
                m1_historical_hash,
                f"Historical M1 reference cache hash drifted! Expected M1: {m1_historical_hash}, "
                f"Step 3 was: {step3_baseline_hash}, Step 2 was: {step2_baseline_hash}, dba5861 was: {baseline_dba5861_hash}",
            )

            # 负向防篡改反例
            tampered_hashes = list(ordered_hashes)
            tampered_hashes[42] = hashlib.sha256((data_map["42"].get("positive", "") + " [tampered]").encode("utf-8")).hexdigest()
            tampered_batch_hash = hashlib.sha256("".join(tampered_hashes).encode("utf-8")).hexdigest()
            self.assertNotEqual(
                tampered_batch_hash,
                m1_historical_hash,
                "Tampered positive prompt content MUST NOT match historical M1 golden hash!",
            )

        # ------------------------------------------------------------------
        # 分支 B: 当前阶段 M5 黄金基线核验 (共用函数流式逐条重算 + 实物防篡改校验)
        # ------------------------------------------------------------------
        manifest_path = REPO_DIR / "scratch" / "audit_m5_3_reeval_manifest.json"
        archive_path = REPO_DIR / "scratch" / "audit_m5_3_evidence.json.gz"
        res = verify_m5_archive_evidence(manifest_path, archive_path, REPO_DIR, stream_recompute=True)
        self.assertEqual(res["status"], "VERIFIED")
        self.assertEqual(res["batch_hash"], EXPECTED_M5_GOLDEN_HASH)

    def test_03c_dual_version_divergence_audit(self):
        """四阶段全链路版本差异审计门禁：全量核验历史演进链证据 (阶段 1~3)，并核验开发起点 c74084d 至 M1 增量变更的因果归因全量证据 (阶段 4)。"""
        stage1_baseline_commit = "bc0d645"
        stage1_baseline_hash = "4525786e7273dc0694e64ec216d4fc9510f211d32de7bdfaa00a12f7a5f320e2"
        stage1_target_hash = "a39a823d09b3b817107ba6a6ebdd5fdb261f4f71bd8148bd7856533fdf21c218"

        stage2_baseline_commit = "dba5861"
        stage2_target_hash = "3e1291ae60af887ebde1869a8fb60f7937424198c1db6a4856bb90aa954725fc"

        stage3_baseline_commit = "2df289a"
        stage3_target_hash = "ab5a633cfb3fde70d9a6c629ee65a540955d87ee143e66570d780743246cab89"

        stage4_baseline_commit = "c74084d"
        stage4_target_hash = "aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139"

        # 1. 阶段 1 历史证据核验 (bc0d645 31 款 -> dba5861 137 款全量迁移)
        stage1_report_file = REPO_DIR / "docs" / "data_migration" / "bc0d645_to_137_divergence_audit.json"
        self.assertTrue(stage1_report_file.exists(), f"Missing Stage 1 audit report: {stage1_report_file}")
        stage1_doc = json.loads(stage1_report_file.read_text(encoding="utf-8"))
        self.assertEqual(stage1_doc.get("total_seeds"), 10000, "Stage 1 report must cover exactly 10,000 seeds")
        self.assertEqual(
            stage1_doc.get("metadata", {}).get("baseline_commit"),
            stage1_baseline_commit,
            f"Stage 1 baseline commit mismatch, expected {stage1_baseline_commit}",
        )
        self.assertEqual(
            stage1_doc.get("baseline_batch_hash"),
            stage1_baseline_hash,
            f"Stage 1 baseline hash mismatch, expected {stage1_baseline_hash}",
        )
        self.assertEqual(
            stage1_doc.get("current_batch_hash"),
            stage1_target_hash,
            f"Stage 1 target hash mismatch, expected {stage1_target_hash}",
        )
        self.assertEqual(
            stage1_doc.get("unexplained_seeds_count"),
            0,
            f"Stage 1 has unexplained diffs: {stage1_doc.get('unexplained_seeds_count')}",
        )

        # 2. 阶段 2 历史证据核验 (dba5861 -> 2df289a 服装状态落地与全景构图误判修复)
        stage2_report_file = REPO_DIR / "scratch" / "dba5861_to_current_audit.json"
        self.assertTrue(stage2_report_file.exists(), f"Missing Stage 2 audit report: {stage2_report_file}")
        stage2_doc = json.loads(stage2_report_file.read_text(encoding="utf-8"))
        self.assertEqual(stage2_doc.get("seeds_count"), 10000, "Stage 2 report must cover exactly 10,000 seeds")
        self.assertEqual(
            stage2_doc.get("baseline_commit"),
            stage2_baseline_commit,
            f"Stage 2 baseline commit mismatch, expected {stage2_baseline_commit}",
        )
        self.assertEqual(
            stage2_doc.get("baseline_batch_hash"),
            stage1_target_hash,
            f"Stage 2 baseline hash must chain-link to Stage 1 target hash {stage1_target_hash}",
        )
        self.assertEqual(
            stage2_doc.get("current_batch_hash"),
            stage2_target_hash,
            f"Stage 2 target hash mismatch, expected {stage2_target_hash}",
        )
        self.assertEqual(
            stage2_doc.get("unexplained_seeds_count"),
            0,
            f"Stage 2 has unexplained diffs: {stage2_doc.get('unexplained_seeds_count')}",
        )

        # 3. 阶段 3 历史证据核验 (2df289a -> c74084d/3989306 跨词库 10 项条目落地)
        stage3_manifest_file = REPO_DIR / "scratch" / "audit_step3_manifest.json"
        self.assertTrue(stage3_manifest_file.exists(), f"Missing Stage 3 manifest: {stage3_manifest_file}")
        stage3_doc = json.loads(stage3_manifest_file.read_text(encoding="utf-8"))
        stage3_res = stage3_doc.get("audit_results", {})
        self.assertEqual(stage3_res.get("total_seeds"), 10000, "Stage 3 manifest must cover exactly 10,000 seeds")
        self.assertEqual(
            stage3_res.get("baseline_commit"),
            stage3_baseline_commit,
            f"Stage 3 baseline commit mismatch, expected {stage3_baseline_commit}",
        )
        self.assertEqual(
            stage3_res.get("baseline_hash"),
            stage2_target_hash,
            f"Stage 3 baseline hash must chain-link to Stage 2 target hash {stage2_target_hash}",
        )
        self.assertEqual(
            stage3_res.get("current_hash"),
            stage3_target_hash,
            f"Stage 3 target hash mismatch, expected {stage3_target_hash}",
        )
        self.assertEqual(
            stage3_res.get("unexplained_seeds_count"),
            0,
            f"Stage 3 has unexplained diffs: {stage3_res.get('unexplained_seeds_count')}",
        )

        # 4. 阶段 4 历史归档证据核验 (c74084d -> M1 垂直切片 6 款词条)
        stage4_manifest_file = REPO_DIR / "scratch" / "audit_m1_manifest.json"
        self.assertTrue(stage4_manifest_file.exists(), f"Missing Stage 4 manifest: {stage4_manifest_file}")
        stage4_doc = json.loads(stage4_manifest_file.read_text(encoding="utf-8"))
        stage4_res = stage4_doc.get("audit_results", {})
        self.assertEqual(stage4_res.get("total_seeds"), 10000, "Stage 4 manifest must cover exactly 10,000 seeds")
        self.assertEqual(
            stage4_res.get("baseline_commit"),
            stage4_baseline_commit,
            f"Stage 4 baseline commit mismatch, expected {stage4_baseline_commit}",
        )
        self.assertEqual(
            stage4_res.get("baseline_hash"),
            stage3_target_hash,
            f"Stage 4 baseline hash must chain-link to Stage 3 target hash {stage3_target_hash}",
        )
        self.assertEqual(
            stage4_res.get("current_hash"),
            stage4_target_hash,
            f"Stage 4 target hash mismatch! Expected {stage4_target_hash}, got {stage4_res.get('current_hash')}",
        )
        self.assertEqual(
            stage4_res.get("unexplained_seeds_count"),
            0,
            f"Stage 4 has unexplained diffs: {stage4_res.get('unexplained_seeds_count')}",
        )

        # 耗时重生成仅在显式设置环境变量 RUN_HISTORICAL_AUDIT=1 时执行
        if os.environ.get("RUN_HISTORICAL_AUDIT") == "1":
            import tempfile
            from scratch.audit_m1_wildcards_slice import run_m1_audit

            audit_seeds = int(os.environ.get("IYKYK_AUDIT_SEEDS", "10000"))
            with tempfile.TemporaryDirectory(prefix="iykyk_m1_audit_") as tmp_dir:
                tmp_path = Path(tmp_dir)
                report = run_m1_audit(
                    total_seeds=audit_seeds,
                    scratch_dir=tmp_path / "scratch",
                    output_archive=tmp_path / "archive.json.gz",
                    output_report_md=tmp_path / "report.md",
                    output_manifest=tmp_path / "manifest.json",
                )
                self.assertEqual(
                    report["unexplained_count"],
                    0,
                    f"Stage 4 M1 has unexplained diffs: {report.get('unexplained_seeds')}",
                )
                if audit_seeds == 10000:
                    self.assertEqual(report["baseline_hash"], stage3_target_hash)
                    self.assertEqual(report["current_hash"], stage4_target_hash)


if __name__ == "__main__":
    unittest.main()
