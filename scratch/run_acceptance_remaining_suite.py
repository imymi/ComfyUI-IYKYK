#!/usr/bin/env python3
"""
Runner for the remaining 44 test files in the acceptance test suite.
Executes test files individually to track exact duration, status, and failures/skips.
"""
import sys
import os
import json
import time
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

COMPLETED_FILES = {
    'test_m5_3_audit_engine.py',
    'test_prompt_diagnostics.py',
    'test_m3_overrides_and_conflict_engine.py',
    'test_m4_slot_wiring_and_legacy_workflows.py',
    'test_conflict_engine_ssot.py',
    'test_conflict_dag_and_invariants.py',
    'test_rc8_quality_gate.py',
    'test_m1_wildcards_slice.py',
    'test_m2_wildcards_batch.py',
}

def main():
    test_dir = REPO_DIR / "tests"
    rem_files = [p for p in sorted(test_dir.glob("test_*.py")) if p.name not in COMPLETED_FILES]
    print(f"Starting test run for {len(rem_files)} files...")
    
    loader = unittest.TestLoader()
    results_summary = []
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    total_errors = 0
    total_discovered = 0
    total_duration = 0.0

    t_all_start = time.time()

    for idx, p in enumerate(rem_files, 1):
        mod_name = f"tests.{p.stem}"
        suite = loader.loadTestsFromName(mod_name)
        count = suite.countTestCases()
        total_discovered += count
        
        t0 = time.time()
        runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=1)
        res = runner.run(suite)
        dur = time.time() - t0
        total_duration += dur

        passed = count - len(res.failures) - len(res.errors) - len(res.skipped)
        total_passed += passed
        total_failed += len(res.failures)
        total_errors += len(res.errors)
        total_skipped += len(res.skipped)

        file_res = {
            "file": p.name,
            "discovered": count,
            "passed": passed,
            "failed": len(res.failures),
            "errors": len(res.errors),
            "skipped": len(res.skipped),
            "duration": round(dur, 3),
            "success": res.wasSuccessful(),
        }
        results_summary.append(file_res)
        print(f"[{idx}/{len(rem_files)}] {p.name}: {count} tests, {dur:.2f}s, success={res.wasSuccessful()}")

    total_wall_time = time.time() - t_all_start

    summary = {
        "files_count": len(rem_files),
        "total_discovered": total_discovered,
        "total_passed": total_passed,
        "total_failed": total_failed,
        "total_errors": total_errors,
        "total_skipped": total_skipped,
        "total_test_duration": round(total_duration, 3),
        "total_wall_time": round(total_wall_time, 3),
        "all_passed": (total_failed == 0 and total_errors == 0),
        "files": results_summary,
    }

    out_file = REPO_DIR / "scratch" / "acceptance_remaining_suite_result.json"
    out_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFinished! Total tests: {total_discovered}, Passed: {total_passed}, Failed: {total_failed}, Skipped: {total_skipped}, Wall time: {total_wall_time:.2f}s")
    sys.exit(0 if summary["all_passed"] else 1)

if __name__ == "__main__":
    main()
