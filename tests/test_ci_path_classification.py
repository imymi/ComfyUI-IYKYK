"""Exercise the actual CI classifier with small, isolated Git histories."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from lib.runtime_manifest import RUNTIME_PACKAGE_FILES


REPO_DIR = Path(__file__).resolve().parent.parent
ARCHIVE_DOCS = ("docs/v1.1.0-rc10_freeze_report.md", "scratch/rc10_freeze_record.md")


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "CI classifier requires Git and Bash")
class TestCIPathClassification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (REPO_DIR / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        step = cls.workflow.split("    - name: Detect docs-only changes\n", 1)[1].split("\n    - ", 1)[0]
        cls.classifier = textwrap.dedent(step.split("      run: |\n", 1)[1])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.revision = 0
        self.git("init", "-q")
        self.git("config", "user.name", "CI Classifier Test")
        self.git("config", "user.email", "ci-classifier@example.invalid")
        self.commit_paths(("README.md",))

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "commit.gpgSign=false", "-c", f"core.hooksPath={os.devnull}", *args],
            cwd=self.repo, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def commit_paths(self, paths):
        self.revision += 1
        for path in paths:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture {self.revision}\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "classification fixture")
        return self.git("rev-parse", "HEAD")

    def classify(self, base):
        output = self.root / "github-output"
        output.write_text("", encoding="utf-8")
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", self.classifier], cwd=self.repo,
            env={**os.environ, "BASE_SHA": base, "RUNNER_TEMP": str(self.root), "GITHUB_OUTPUT": str(output)},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output.read_text(encoding="utf-8").strip()

    def assert_paths(self, paths, expected):
        base = self.git("rev-parse", "HEAD")
        self.commit_paths(paths)
        self.assertEqual(self.classify(base), f"docs_only={str(expected).lower()}")

    def test_release_archive_docs_use_lightweight_gate(self):
        self.assertTrue(set(ARCHIVE_DOCS).isdisjoint(RUNTIME_PACKAGE_FILES))
        for paths in ((ARCHIVE_DOCS[0],), (ARCHIVE_DOCS[1],), ARCHIVE_DOCS):
            with self.subTest(paths=paths):
                self.assert_paths(paths, True)

    def test_existing_docs_paths_remain_lightweight(self):
        for path in ("README.md", "CHANGELOG.md", "THIRD_PARTY.md", "docs/guide.md", "source_md/source.md"):
            with self.subTest(path=path):
                self.assert_paths((path,), True)

    def test_other_scratch_paths_and_non_markdown_require_full_gate(self):
        for path in (
            "scratch/rc11_freeze_record.md", "scratch/other.md", "scratch/nested/rc10_freeze_record.md",
            "scratch/rc10_freeze_record.md.py", "scratch/rc10_freeze_record.md.json",
            "scratch/audit_m1_wildcards_slice.py", "scratch/rc10_manifest.json",
            "docs/check.py", "docs/data.json", "source_md/check.py", "source_md/data.json",
            ".github/workflows/ci.yml", ".github/workflows/release.yml", "tests/test_ci_path_classification.py",
        ):
            with self.subTest(path=path):
                self.assert_paths((path,), False)

    def test_runtime_paths_require_full_gate(self):
        # README/CHANGELOG already use documentation contracts and package verification.
        for path in set(RUNTIME_PACKAGE_FILES) - {"README.md", "CHANGELOG.md"}:
            with self.subTest(path=path):
                self.assert_paths((path,), False)

    def test_mixed_changes_require_full_gate_in_either_path_order(self):
        for path in (".github/workflows/ci.yml", "nodes.py", "data/clothing.json", "scratch/audit.py"):
            with self.subTest(path=path):
                self.assert_paths((*ARCHIVE_DOCS, path), False)

    def test_nul_delimited_paths_cannot_hide_non_docs(self):
        self.assert_paths(("docs/guide with spaces.md",), True)
        self.assert_paths((*ARCHIVE_DOCS, "scratch/rc10_freeze_record.md\nnodes.py"), False)

    def test_missing_invalid_and_empty_bases_require_full_gate(self):
        self.commit_paths(ARCHIVE_DOCS)
        for base in ("", "invalid", "0" * 40, "f" * 40, self.git("rev-parse", "HEAD")):
            with self.subTest(base=base):
                self.assertEqual(self.classify(base), "docs_only=false")

    def test_runtime_deletions_and_renames_require_full_gate(self):
        for old, new in (("nodes.py", ARCHIVE_DOCS[1]), (ARCHIVE_DOCS[1], "nodes.py")):
            with self.subTest(old=old, new=new):
                base = self.commit_paths((old,))
                target = self.repo / new
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)
                self.git("mv", old, new)
                self.git("commit", "-qm", "rename fixture")
                self.assertEqual(self.classify(base), "docs_only=false")
        base = self.commit_paths(("nodes.py",))
        self.git("rm", "nodes.py")
        self.git("commit", "-qm", "delete fixture")
        self.assertEqual(self.classify(base), "docs_only=false")

    def test_full_and_lightweight_quality_gates_remain_wired(self):
        regression = self.workflow.split("\n  compatibility:", 1)[0]
        for name in (
            "Run Ruff Lint", "Check Rule Schema Drift (Read-only)", "Validate Datasets & Schema",
            "Generate M1 Divergence Audit Evidence & Verify Manifest",
            "Generate M2 Divergence Audit Evidence & Verify Manifest",
            "Run Full Test Suite", "Double Build & Compare SHA256",
        ):
            with self.subTest(step=name):
                step = regression.split(f"    - name: {name}\n", 1)[1].split("\n    - ", 1)[0]
                self.assertIn("      if: steps.changes.outputs.docs_only != 'true'\n", step)
        for name in ("Check documentation contracts", "Verify documentation release package"):
            step = regression.split(f"    - name: {name}\n", 1)[1].split("\n    - ", 1)[0]
            self.assertIn("      if: steps.changes.outputs.docs_only == 'true' && matrix.python-version == '3.11'\n", step)
        self.assertIn(
            "    - name: Check CI path classification\n"
            "      if: matrix.python-version == '3.11'\n"
            "      run: python -m unittest tests.test_ci_path_classification -v\n", regression,
        )


if __name__ == "__main__":
    unittest.main()
