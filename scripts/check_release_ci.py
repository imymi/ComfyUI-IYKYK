"""Reuse only complete main CI for the exact release commit; otherwise run full CI."""
from __future__ import annotations

import json
import os
import re
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REGRESSION_STEPS = (
    "Run Ruff Lint", "Check Rule Schema Drift (Read-only)", "Validate Datasets & Schema",
    "Generate M1 Divergence Audit Evidence & Verify Manifest",
    "Generate M2 Divergence Audit Evidence & Verify Manifest",
    "Run Full Test Suite", "Double Build & Compare SHA256",
)
COMPATIBILITY_STEPS = (
    "Validate Datasets & Schema", "Verify Import & Node Registration", "Basic Generation & Smoke Tests",
)
REQUIRED_JOBS = {
    "Full Regression (3.11)": (*REGRESSION_STEPS, "Check CI path classification", "Check release workflow gates"),
    "Full Regression (3.12)": REGRESSION_STEPS,
    "Compatibility Check (3.9)": COMPATIBILITY_STEPS,
    "Compatibility Check (3.10)": COMPATIBILITY_STEPS,
}


def api_json(path, **params):
    query = "?" + urlencode(params) if params else ""
    request = Request("https://api.github.com" + path + query, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def pages(get, path, key, **params):
    items = []
    page = 1
    while True:
        batch = get(path, per_page=100, page=page, **params)[key]
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def trusted_run(run, repo, sha):
    return (
        run.get("head_sha") == sha and run.get("event") == "push" and run.get("head_branch") == "main"
        and run.get("path") == ".github/workflows/ci.yml"
        and run.get("repository", {}).get("full_name") == repo
        and run.get("head_repository", {}).get("full_name") == repo
    )


def complete_gates(jobs, sha):
    # A successful workflow can contain skipped jobs/steps (e.g. docs-only CI).
    if any(job.get("conclusion") != "success" or job.get("head_sha") != sha for job in jobs):
        return False
    for name, required_steps in REQUIRED_JOBS.items():
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1 or matches[0].get("status") != "completed":
            return False
        steps = matches[0].get("steps", [])
        for required in required_steps:
            matches = [step for step in steps if step.get("name") == required]
            if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
                return False
    return True


def reusable_ci(repo, sha, get=api_json, wait_seconds=240 * 60):
    base = f"/repos/{repo}/actions"
    runs = pages(get, f"{base}/workflows/ci.yml/runs", "workflow_runs", head_sha=sha, branch="main", event="push")
    runs = [run for run in runs if trusted_run(run, repo, sha)]
    if not runs:
        return None
    # Do not silently choose an older success over a newer failing/running attempt.
    run = max(runs, key=lambda item: item["id"])
    run_id = run["id"]
    deadline = time.monotonic() + wait_seconds
    while run.get("status") in ("queued", "in_progress", "waiting", "pending", "requested"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        print(f"Waiting for CI run {run_id} on {sha}...", flush=True)
        time.sleep(min(60, remaining))
        run = get(f"{base}/runs/{run_id}")
        if not trusted_run(run, repo, sha):
            return None
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return None
    attempt = run["run_attempt"]
    jobs = pages(get, f"{base}/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    if not complete_gates(jobs, sha):
        return None
    # A rerun must not mix job evidence from different attempts.
    current = get(f"{base}/runs/{run_id}")
    if (not trusted_run(current, repo, sha) or current.get("run_attempt") != attempt
            or current.get("status") != "completed" or current.get("conclusion") != "success"):
        return None
    return run_id


def main():
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid release repository or commit")
    try:
        run_id = reusable_ci(repo, sha)
    except (URLError, TimeoutError, ValueError, KeyError, TypeError) as error:
        print(f"::warning::CI evidence unavailable ({type(error).__name__}); running full quality gates.")
        run_id = None
    message = f"Reused complete CI: https://github.com/{repo}/actions/runs/{run_id}" if run_id else "No reusable complete CI; running full quality gates."
    print(message, flush=True)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"reused={str(run_id is not None).lower()}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(f"{message}\nRelease commit: `{sha}`\n")


if __name__ == "__main__":
    main()
