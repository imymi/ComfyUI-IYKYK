"""Contracts for live CI evidence and the separate historical M5 archive."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_m5_3_audit_engine as audit_tests
from tests import test_rc8_quality_gate as quality_gate


class TestM5CIEvidenceModes(unittest.TestCase):
    def run_archive_test(self, root: Path, env: dict[str, str], *, make_archive_directory: bool = False) -> unittest.TestResult:
        (root / "scratch").mkdir()
        (root / "scratch" / "audit_m5_3_reeval_manifest.json").write_text("{}", encoding="utf-8")
        if make_archive_directory:
            (root / "scratch" / "audit_m5_3_evidence.json.gz").mkdir()
        case = audit_tests.TestM5EvidenceVerification("test_17_real_production_evidence_fast_metadata_verified")
        result = unittest.TestResult()
        with patch.object(audit_tests, "REPO_DIR", root), patch.dict(os.environ, env, clear=True):
            case.run(result)
        return result

    def test_live_mode_explicitly_skips_only_unconfigured_missing_history(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_archive_test(Path(temp), {"RUN_LIVE_SEED_GATE": "1"})
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("Historical M5 archive unavailable", result.skipped[0][1])
        self.assertFalse(result.failures or result.errors)

    def test_missing_archive_fails_when_configured_or_when_using_archive_mode(self):
        for env in ({}, {"RUN_LIVE_SEED_GATE": "1", "M5_ARCHIVE_PATH": "/missing/m5-evidence.gz"}):
            with self.subTest(env=env), tempfile.TemporaryDirectory() as temp:
                result = self.run_archive_test(Path(temp), env)
                self.assertEqual(len(result.failures), 1)
                self.assertFalse(result.skipped or result.errors)
                self.assertIn("Production M5 evidence archive must exist", result.failures[0][1])

    def test_empty_explicit_archive_path_is_rejected(self):
        with patch.dict(os.environ, {"M5_ARCHIVE_PATH": ""}, clear=True):
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                quality_gate.m5_archive_path(Path("/tmp/repo"))

    def test_live_mode_does_not_ignore_a_present_invalid_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "invalid.gz"
            archive.write_bytes(b"not an archive")
            result = self.run_archive_test(root, {"RUN_LIVE_SEED_GATE": "1", "M5_ARCHIVE_PATH": str(archive)})
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(result.skipped or result.errors)
        self.assertIn("source commit or tree", result.failures[0][1])

    def test_live_mode_does_not_skip_a_non_file_archive_path(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_archive_test(Path(temp), {"RUN_LIVE_SEED_GATE": "1"}, make_archive_directory=True)
        self.assertEqual(len(result.failures), 1)
        self.assertFalse(result.skipped or result.errors)

    def test_live_golden_hash_is_required_even_without_historical_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = quality_gate.TestRC8QualityGate("test_03b_seed_gate_golden_hash")
            with patch.object(quality_gate, "REPO_DIR", root), patch.dict(
                os.environ, {"RUN_LIVE_SEED_GATE": "1"}, clear=True
            ):
                with patch.object(quality_gate.TestRC8QualityGate, "_run_seed_gate_10k", return_value=(None, 10000, "0" * 64)):
                    with self.assertRaisesRegex(AssertionError, "Current live seed hash differs"):
                        case.test_03b_seed_gate_golden_hash()
                with patch.object(
                    quality_gate.TestRC8QualityGate, "_run_seed_gate_10k",
                    return_value=(None, 10000, quality_gate.EXPECTED_V1_2_1_GOLDEN_HASH),
                ):
                    case.test_03b_seed_gate_golden_hash()
                    with patch.dict(os.environ, {"M5_ARCHIVE_PATH": str(root / "missing.gz")}):
                        with self.assertRaisesRegex(AssertionError, "Historical M5 evidence archive must exist"):
                            case.test_03b_seed_gate_golden_hash()

    def test_live_seed_hash_rejects_missing_or_duplicate_seeds(self):
        self.assertEqual(
            quality_gate.hash_complete_seed_batch([(1, "b"), (0, "a")], 2),
            hashlib.sha256(b"ab").hexdigest(),
        )
        for pairs in ([(0, "a")], [(0, "a"), (0, "b")], [(0, "a"), (2, "b")]):
            with self.subTest(pairs=pairs), self.assertRaisesRegex(AssertionError, "exactly once"):
                quality_gate.hash_complete_seed_batch(pairs, 2)

    def test_historical_manifest_must_bind_the_original_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"auditor_binding": {
                "target_commit": "0" * 40,
                "target_tree_hash": quality_gate.EXPECTED_M5_SOURCE_TREE,
            }}), encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "source commit or tree"):
                quality_gate.verify_m5_historical_archive(manifest, root / "archive.gz", root, stream_recompute=False)

    def test_historical_commit_must_resolve_to_the_frozen_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"auditor_binding": {
                "target_commit": quality_gate.EXPECTED_M5_SOURCE_COMMIT,
                "target_tree_hash": quality_gate.EXPECTED_M5_SOURCE_TREE,
            }}), encoding="utf-8")
            with patch.object(quality_gate.subprocess, "run", return_value=SimpleNamespace(stdout="0" * 40)):
                with self.assertRaisesRegex(AssertionError, "source commit tree"):
                    quality_gate.verify_m5_historical_archive(manifest, root / "archive.gz", root, stream_recompute=False)

    def test_historical_source_extraction_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"auditor_binding": {
                "target_commit": quality_gate.EXPECTED_M5_SOURCE_COMMIT,
                "target_tree_hash": quality_gate.EXPECTED_M5_SOURCE_TREE,
            }}), encoding="utf-8")
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                archive.addfile(tarfile.TarInfo("../escape"))
            results = [SimpleNamespace(stdout=quality_gate.EXPECTED_M5_SOURCE_TREE), SimpleNamespace(stdout=buffer.getvalue())]
            with patch.object(quality_gate.subprocess, "run", side_effect=results):
                with self.assertRaisesRegex(AssertionError, "Unsafe path"):
                    quality_gate.verify_m5_historical_archive(manifest, root / "archive.gz", root, stream_recompute=False)

    def test_live_worker_rejects_nondeterministic_diagnostic_json(self):
        original = quality_gate.nodes.IYKYKPromptDiagnostics.diagnose
        calls = 0

        def mutate_second_diagnostic(node, *args, **kwargs):
            nonlocal calls
            result = original(node, *args, **kwargs)
            calls += 1
            if calls == 2:
                return (*result[:3], result[3] + " ")
            return result

        with patch.object(quality_gate.nodes.IYKYKPromptDiagnostics, "diagnose", new=mutate_second_diagnostic):
            _, _, hashes, first_failure = quality_gate._worker_seed_batch(0, 1)
        self.assertEqual(first_failure, 0)
        self.assertEqual(hashes, [])

    def test_full_ci_explicitly_runs_live_seed_gate(self):
        workflow = (quality_gate.REPO_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        step = workflow.split("    - name: Run Full Test Suite\n", 1)[1].split("\n    - ", 1)[0]
        self.assertIn('        RUN_LIVE_SEED_GATE: "1"\n', step)
        self.assertIn("python -m unittest discover -s tests -v", step)


if __name__ == "__main__":
    unittest.main()
