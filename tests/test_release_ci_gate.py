"""Exercise CI reuse decisions with API fixtures, without network or slow audits."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from scripts import check_release_ci as gate


REPO = "imymi/ComfyUI-IYKYK"
SHA = "a" * 40
ROOT = Path(__file__).resolve().parent.parent


def evidence():
    run = {
        "id": 7, "run_attempt": 2, "head_sha": SHA, "path": ".github/workflows/ci.yml",
        "event": "push", "head_branch": "main", "status": "completed", "conclusion": "success",
        "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
    }
    regression = (
        "Run Ruff Lint", "Check Rule Schema Drift (Read-only)", "Validate Datasets & Schema",
        "Generate M1 Divergence Audit Evidence & Verify Manifest",
        "Generate M2 Divergence Audit Evidence & Verify Manifest",
        "Run Full Test Suite", "Double Build & Compare SHA256",
    )
    compatibility = (
        "Validate Datasets & Schema", "Verify Import & Node Registration", "Basic Generation & Smoke Tests",
    )
    required_jobs = {
        "Full Regression (3.11)": (*regression, "Check CI path classification", "Check release workflow gates"),
        "Full Regression (3.12)": regression,
        "Compatibility Check (3.9)": compatibility,
        "Compatibility Check (3.10)": compatibility,
    }
    jobs = [{
        "name": name, "head_sha": SHA, "status": "completed", "conclusion": "success",
        "steps": [{"name": step, "status": "completed", "conclusion": "success"} for step in steps],
    } for name, steps in required_jobs.items()]
    return run, jobs


class TestReleaseCIGate(unittest.TestCase):
    def setUp(self):
        self.run, self.jobs = evidence()

    def lookup(self, path, **params):
        if path.endswith("/workflows/ci.yml/runs"):
            self.assertEqual(params, {"head_sha": SHA, "branch": "main", "event": "push", "per_page": 100, "page": 1})
            return {"workflow_runs": [self.run]}
        if path.endswith("/attempts/2/jobs"):
            return {"jobs": self.jobs}
        self.assertTrue(path.endswith("/runs/7"), path)
        return self.run

    def test_complete_same_commit_ci_is_reusable(self):
        self.assertEqual(gate.reusable_ci(REPO, SHA, get=self.lookup), 7)

    def test_wrong_commit_event_branch_workflow_and_fork_are_rejected(self):
        for field, value in (
            ("head_sha", "b" * 40), ("event", "pull_request"), ("head_branch", "feature"),
            ("path", ".github/workflows/other.yml"), ("repository", {"full_name": "other/repo"}),
            ("head_repository", {"full_name": "other/repo"}),
        ):
            with self.subTest(field=field), patch.dict(self.run, {field: value}):
                self.assertIsNone(gate.reusable_ci(REPO, SHA, get=self.lookup))

    def test_failed_cancelled_and_missing_runs_are_rejected(self):
        for conclusion in ("failure", "cancelled", "timed_out", "neutral", None):
            with self.subTest(conclusion=conclusion), patch.dict(self.run, {"conclusion": conclusion}):
                self.assertIsNone(gate.reusable_ci(REPO, SHA, get=self.lookup))
        self.assertIsNone(gate.reusable_ci(REPO, SHA, get=lambda *args, **kwargs: {"workflow_runs": []}))

    def test_each_required_gate_must_succeed_without_skips(self):
        for index, job in enumerate(self.jobs):
            for step_index, step in enumerate(job["steps"]):
                for conclusion in ("skipped", "failure", "cancelled", None):
                    with self.subTest(job=job["name"], step=step["name"], conclusion=conclusion):
                        jobs = copy.deepcopy(self.jobs)
                        jobs[index]["steps"][step_index]["conclusion"] = conclusion
                        self.assertFalse(gate.complete_gates(jobs, SHA))

    def test_missing_duplicate_incomplete_and_failed_jobs_are_rejected(self):
        for index in range(len(self.jobs)):
            with self.subTest(index=index):
                self.assertFalse(gate.complete_gates(self.jobs[:index] + self.jobs[index + 1:], SHA))
        self.assertFalse(gate.complete_gates(self.jobs + [self.jobs[0]], SHA))
        for field, value in (("status", "in_progress"), ("conclusion", "skipped"), ("head_sha", "b" * 40)):
            with patch.dict(self.jobs[0], {field: value}):
                self.assertFalse(gate.complete_gates(self.jobs, SHA))
        step = self.jobs[0]["steps"].pop()
        self.assertFalse(gate.complete_gates(self.jobs, SHA))
        self.jobs[0]["steps"].extend([step, step])
        self.assertFalse(gate.complete_gates(self.jobs, SHA))

    def test_latest_ci_failure_cannot_be_hidden_by_older_success(self):
        older = {**self.run, "id": 6}
        self.run["conclusion"] = "failure"
        get = Mock(return_value={"workflow_runs": [older, self.run]})
        self.assertIsNone(gate.reusable_ci(REPO, SHA, get=get))
        get.assert_called_once()

    def test_pending_ci_is_waited_for_and_reused(self):
        pending = {**self.run, "status": "in_progress", "conclusion": None}
        get = Mock(side_effect=[{"workflow_runs": [pending]}, self.run, {"jobs": self.jobs}, self.run])
        with patch.object(gate.time, "sleep") as sleep:
            self.assertEqual(gate.reusable_ci(REPO, SHA, get=get), 7)
        sleep.assert_called_once_with(60)

    def test_pending_ci_timeout_falls_back(self):
        self.run.update(status="in_progress", conclusion=None)
        with patch.object(gate.time, "sleep") as sleep:
            self.assertIsNone(gate.reusable_ci(REPO, SHA, get=self.lookup, wait_seconds=0))
        sleep.assert_not_called()

    def test_rerun_cannot_mix_attempts_or_publish_cancelled_ci(self):
        for changes in ({"run_attempt": 3}, {"status": "in_progress"}, {"conclusion": "cancelled"}):
            get = Mock(side_effect=[{"workflow_runs": [self.run]}, {"jobs": self.jobs}, {**self.run, **changes}])
            self.assertIsNone(gate.reusable_ci(REPO, SHA, get=get))

    def test_run_and_job_pagination_is_complete(self):
        get = Mock(side_effect=[{"workflow_runs": [self.run] * 100}, {"workflow_runs": []},
                                {"jobs": [{"name": "extra", "head_sha": SHA, "conclusion": "success"}] * 100},
                                {"jobs": self.jobs}, self.run])
        self.assertEqual(gate.reusable_ci(REPO, SHA, get=get), 7)
        self.assertEqual([call.kwargs["page"] for call in get.call_args_list[:-1]], [1, 2, 1, 2])

    def test_api_errors_fall_back_and_record_commit_without_token(self):
        with tempfile.TemporaryDirectory() as temp:
            output, summary = Path(temp) / "output", Path(temp) / "summary"
            env = {"GITHUB_REPOSITORY": REPO, "GITHUB_SHA": SHA,
                   "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
            with patch.dict(os.environ, env), patch.object(gate, "reusable_ci", side_effect=URLError("offline")):
                gate.main()
            self.assertEqual(output.read_text(), "reused=false\n")
            self.assertIn(SHA, summary.read_text())

    def test_workflow_fallback_and_publication_are_wired(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text()
        release = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("  workflow_call:\n", ci)
        self.assertIn("        FORCE_FULL: ${{ inputs.force_full }}\n", ci)
        self.assertIn("    uses: ./.github/workflows/ci.yml\n    with:\n      force_full: true\n", release)
        self.assertIn("    needs: [resolve_ci, quality_gates]\n", release)
        self.assertIn("!cancelled() && needs.resolve_ci.result == 'success'", release)
        self.assertIn("needs.resolve_ci.outputs.reused == 'true' && needs.quality_gates.result == 'skipped'", release)
        self.assertIn("needs.quality_gates.result == 'success'", release)
        self.assertEqual(release.count("ref: ${{ github.sha }}"), 2)
        self.assertEqual(ci.count("ref: ${{ github.sha }}"), 2)
        self.assertNotIn("unittest discover", release)
        self.assertIn("--mode release --output-dir dist", release)
        self.assertLess(release.index("--draft "), release.index("gh release download"))
        self.assertLess(release.index('cmp "dist/$GEN_DIR/$asset"'), release.index("--draft=false"))

    def test_publication_requires_matching_tag_and_all_uploaded_bytes(self):
        release = (ROOT / ".github/workflows/release.yml").read_text()
        publish = textwrap.dedent(release.split("    - name: Create GitHub Release\n", 1)[1].split("      run: |\n", 1)[1])
        for scenario in ("valid", "missing", "corrupt", "extra", "existing", "wrong_tag", "moved_tag"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                assets = root / "dist" / "generation"
                assets.mkdir(parents=True)
                for name in ("package.zip", "MANIFEST.json", "SHA256SUMS.txt"):
                    (assets / name).write_bytes(name.encode())
                (root / "dist" / "CURRENT.json").write_text(json.dumps({
                    "generation_dir": "generation", "release_file": "package.zip", "version": "v1.2.0",
                }))
                bin_dir = root / "bin"
                bin_dir.mkdir()
                (bin_dir / "python").symlink_to(sys.executable)
                (bin_dir / "git").write_text('#!/bin/sh\nprintf "%s\\trefs/tags/v1.2.0^{}\\n" "$REMOTE_SHA"\n')
                (bin_dir / "gh").write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
                    import os, shutil, sys
                    from pathlib import Path
                    args = sys.argv[1:]
                    with open('calls', 'a') as log:
                        log.write(' '.join(args) + '\\n')
                    scenario = os.environ['SCENARIO']
                    if args[1] == 'create' and scenario == 'existing':
                        sys.exit(1)
                    if args[1] == 'download':
                        destination = Path(args[args.index('--dir') + 1])
                        for source in Path('dist/generation').iterdir():
                            if scenario != 'missing' or source.name != 'MANIFEST.json':
                                shutil.copyfile(source, destination / source.name)
                        if scenario == 'corrupt':
                            (destination / 'package.zip').write_bytes(b'corrupt')
                        if scenario == 'extra':
                            (destination / 'extra.txt').write_bytes(b'extra')
                    '''))
                for name in ("gh", "git"):
                    (bin_dir / name).chmod(0o755)
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", publish], cwd=root,
                                        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                                             "RUNNER_TEMP": temp, "GITHUB_SHA": SHA, "SCENARIO": scenario,
                                             "GITHUB_REF_NAME": "v9.0.0" if scenario == "wrong_tag" else "v1.2.0",
                                             "REMOTE_SHA": "b" * 40 if scenario == "moved_tag" else SHA},
                                        capture_output=True, text=True)
                calls = (root / "calls").read_text() if (root / "calls").exists() else ""
                if scenario == "valid":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("--draft --generate-notes", calls)
                    self.assertIn("release edit v1.2.0 --draft=false", calls)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("release edit", calls)


if __name__ == "__main__":
    unittest.main()
