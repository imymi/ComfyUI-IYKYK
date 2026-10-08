import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path("/Users/jacobyang/Hermes/ComfyUI-IYKYK")

def make_clean_git_repo(target_dir: Path) -> Path:
    exclude_patterns = {".git", "__pycache__", ".pytest_cache", "dist", ".venv"}
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in REPO_DIR.iterdir():
        if item.name in exclude_patterns or item.name.startswith(".staging"):
            continue
        dest = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(item, dest)

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Release Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    subprocess.run(["git", "init"], cwd=target_dir, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "--local", "user.name", "Release Tester"], cwd=target_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "--local", "user.email", "tester@example.com"], cwd=target_dir, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=target_dir, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "Initial clean snapshot for test"], cwd=target_dir, check=True, capture_output=True, env=env)
    return target_dir

def main():
    print("=== RC10 Independent Dual Release Build Verification ===")
    out_a = Path("/tmp/comfyui_iykyk_build_a")
    out_b = Path("/tmp/comfyui_iykyk_build_b")
    repo_a = Path("/tmp/comfyui_iykyk_repo_a")
    repo_b = Path("/tmp/comfyui_iykyk_repo_b")

    for p in (out_a, out_b, repo_a, repo_b):
        if p.exists():
            shutil.rmtree(p)

    print("1. Creating clean candidate snapshot repo A...")
    make_clean_git_repo(repo_a)
    print("2. Creating clean candidate snapshot repo B...")
    make_clean_git_repo(repo_b)

    print("3. Executing Build A (--mode verify --output-dir /tmp/comfyui_iykyk_build_a)...")
    res_a = subprocess.run(
        [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", str(out_a)],
        cwd=repo_a,
        capture_output=True,
        text=True
    )
    if res_a.returncode != 0:
        print(f"[FAIL] Build A failed:\n{res_a.stderr}\n{res_a.stdout}")
        sys.exit(1)
    print("   Build A succeeded.")

    print("4. Executing Build B (--mode verify --output-dir /tmp/comfyui_iykyk_build_b)...")
    res_b = subprocess.run(
        [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", str(out_b)],
        cwd=repo_b,
        capture_output=True,
        text=True
    )
    if res_b.returncode != 0:
        print(f"[FAIL] Build B failed:\n{res_b.stderr}\n{res_b.stdout}")
        sys.exit(1)
    print("   Build B succeeded.")

    # Inspect pointers
    ptr_a = json.loads((out_a / "CURRENT.json").read_text(encoding="utf-8"))
    ptr_b = json.loads((out_b / "CURRENT.json").read_text(encoding="utf-8"))

    zip_a = out_a / ptr_a["generation_dir"] / "ComfyUI-IYKYK.zip"
    zip_b = out_b / ptr_b["generation_dir"] / "ComfyUI-IYKYK.zip"

    sha_a = hashlib.sha256(zip_a.read_bytes()).hexdigest()
    sha_b = hashlib.sha256(zip_b.read_bytes()).hexdigest()

    manifest_a = json.loads((out_a / ptr_a["generation_dir"] / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest_b = json.loads((out_b / ptr_b["generation_dir"] / "MANIFEST.json").read_text(encoding="utf-8"))

    print("\n=== Verification Results ===")
    print(f"Build A Zip: {zip_a}")
    print(f"  SHA256: {sha_a}")
    print(f"  Files Count: {manifest_a['files_count']}")
    print(f"Build B Zip: {zip_b}")
    print(f"  SHA256: {sha_b}")
    print(f"  Files Count: {manifest_b['files_count']}")

    if sha_a == sha_b:
        print(f"\n✅ DUAL BUILD BIT-FOR-BIT IDENTICAL: SHA256 MATCHES PERFECTLY ({sha_a})")
    else:
        print("\n❌ DUAL BUILD MISMATCH!")
        sys.exit(1)

if __name__ == "__main__":
    main()
