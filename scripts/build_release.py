#!/usr/bin/env python3
"""
build_release.py — ComfyUI-IYKYK 确定性可重复构建与纯净打包脚本

特性：
1. 双模式支持：--mode=verify (默认, CI 校验) 与 --mode=release (正式发布, 强制检查 annotated tag)
2. 强制 clean-tree 检查，无任何绕过入口，dirty 工作区直接 Fail-Closed
3. 动态从 pyproject.toml 读取版本并对 js/version.js 执行只读校验
4. 打包前执行 validate_data.py --strict 强门禁校验
5. 严格白名单过滤：打包 38 个核心运行时文件（6 root + 2 js + 11 lib + 19 data）
6. 规范化 ZIP 文件时间戳（由 HEAD commit timestamp 或 SOURCE_DATE_EPOCH 驱动）与权限
7. 同时生成 versioned, latest 与 generic 3 个二进制一致的 ZIP 产物
8. 计算 per-file SHA256，生成 MANIFEST.json 与 SHA256SUMS.txt
9. 在独立 generation 临时目录构建与校验，执行隔离环境烟雾测试
10. 方案 A：不可变版本目录 v{version}-{archive_sha12} + 原子指针 CURRENT.json 严格状态机发布协议
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import stat
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Optional

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.runtime_manifest import RUNTIME_PACKAGE_FILES, TOTAL_RUNTIME_FILES_COUNT


class PlatformNotSupportedError(RuntimeError):
    """跨平台排他文件锁不支持当前运行平台时抛出。"""
    pass


EXPECTED_MANIFEST_KEYS = {
    "extension_name",
    "version",
    "mode",
    "source_commit",
    "generated_at",
    "release_file",
    "sha256",
    "files_count",
    "files",
    "files_sha256",
}


def validate_and_align_timestamp(commit_ts: int) -> tuple[int, tuple[int, int, int, int, int, int], str]:
    """计算并校验对齐的 SOURCE_DATE_EPOCH 与符合规范的 ZIP 基础时间戳。"""
    if "SOURCE_DATE_EPOCH" in os.environ:
        try:
            commit_ts = int(os.environ["SOURCE_DATE_EPOCH"])
        except ValueError:
            raise ValueError(f"Invalid SOURCE_DATE_EPOCH value: {os.environ['SOURCE_DATE_EPOCH']}")

    # 范围验证：1980-01-01 至 2107-12-31 (ZIP 格式 MS-DOS 时间限制)
    MIN_EPOCH = 315532800   # 1980-01-01 00:00:00 UTC
    MAX_EPOCH = 4354819200  # 2107-12-31 23:59:58 UTC
    if commit_ts < MIN_EPOCH or commit_ts > MAX_EPOCH:
        raise ValueError(
            f"SOURCE_DATE_EPOCH {commit_ts} out of valid ZIP range (1980-2107): "
            f"must be between {MIN_EPOCH} and {MAX_EPOCH}"
        )

    # ZIP 时间戳以 2 秒为精度步长，对齐为偶数秒
    aligned_ts = commit_ts if commit_ts % 2 == 0 else commit_ts - 1
    dt = datetime.datetime.fromtimestamp(aligned_ts, tz=datetime.timezone.utc)
    zip_ts = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    iso_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return aligned_ts, zip_ts, iso_timestamp


def fsync_dir(dir_path: Path) -> None:
    """物理刷新目录元数据到磁盘。Fail-Closed 严禁静默吞掉 OSError。"""
    if os.name == "posix":
        try:
            fd = os.open(str(dir_path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            raise RuntimeError(f"Failed to fsync directory {dir_path}: {e}") from e


class ReleaseLock:
    """跨平台进程排他文件锁 (POSIX 使用 fcntl, Windows 使用 msvcrt)。"""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.name == "nt":
                import msvcrt
                import time
                deadline = time.time() + 60.0
                while True:
                    try:
                        msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.time() > deadline:
                            raise TimeoutError(f"Failed to acquire release lock on {self.lock_path} within 60s")
                        time.sleep(0.05)
            elif os.name == "posix":
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            else:
                raise PlatformNotSupportedError(f"Unsupported operating system '{os.name}' for release locking!")
        except Exception:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                elif os.name == "posix":
                    import fcntl
                    try:
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
            finally:
                os.close(self._fd)
                self._fd = None


def validate_zip_entry_integrity(zip_p: Path, expected_files_sha: dict[str, str]) -> None:
    """严格深度校验单个 ZIP 包内实物：testzip()、Unix 文件类型位、无目录/软链接/重复路径，逐文件比对 hash (R3)。"""
    zip_name = zip_p.name
    with zipfile.ZipFile(zip_p, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise ValueError(f"Corrupted entry in {zip_name}: {bad_file}")

        infolist = zf.infolist()
        z_namelist = [info.filename for info in infolist]

        # 检查重复路径 (R3)
        if len(z_namelist) != len(set(z_namelist)):
            raise ValueError(f"Duplicate paths detected in {zip_name}")

        # 严格使用 stat 检查 Unix 文件类型位 (R3)
        for info in infolist:
            mode = info.external_attr >> 16
            if info.is_dir() or info.filename.endswith("/") or stat.S_ISDIR(mode):
                raise ValueError(f"Forbidden directory entry detected in {zip_name}: {info.filename}")
            if stat.S_ISLNK(mode):
                raise ValueError(f"Forbidden symlink entry detected in {zip_name}: {info.filename}")
            if not stat.S_ISREG(mode):
                raise ValueError(f"Non-regular file entry detected in {zip_name}: {info.filename} (mode={oct(mode)})")

        if len(infolist) != len(expected_files_sha):
            raise ValueError(
                f"{zip_name} internal files count ({len(infolist)}) mismatch with manifest ({len(expected_files_sha)})"
            )

        if set(z_namelist) != set(expected_files_sha.keys()):
            raise ValueError(f"{zip_name} internal files do not match manifest files_sha256")
        for item_name in z_namelist:
            item_bytes = zf.read(item_name)
            item_sha = hashlib.sha256(item_bytes).hexdigest()
            expected_item_sha = expected_files_sha[item_name]
            if item_sha != expected_item_sha:
                raise ValueError(
                    f"File {item_name} in {zip_name} has corrupted hash {item_sha}, expected {expected_item_sha}"
                )


def validate_generation_integrity(
    gen_dir: Path,
    expected_version: str,
    expected_sha: str,
    expected_mode: str,
    expected_source_commit: Optional[str] = None,
    expected_files_sha: Optional[dict[str, str]] = None,
) -> None:
    """
    严格 Fail-Closed 强校验 (P1-1 终验防伪闭环)：
    1. 严格目录条目级白名单：拒绝子目录、符号链接、特殊文件与多余/缺失文件；
    2. 校验不可变 generation 目录名必须完全匹配 v{version}-{sha12}；
    3. 校验 3 个 ZIP 文件实物存在且其实际 SHA256 完全相等且等于 expected_sha；
    4. 严格解析 SHA256SUMS.txt 格式与实物 hash 绝对一致；
    5. 严格验证 MANIFEST.json 全字段类型、集合、无路径穿越、files_count 与 files_sha256 绝对一致；
    6. 深度校验 ZIP 包内实物：testzip() 无损坏，解包逐个文件比对实际 hash 与 manifest.files_sha256 完全一致。
    任何校验不通过立即抛出 ValueError (Fail-Closed)，绝对禁止更新 CURRENT.json。
    """
    if not gen_dir.is_dir() or gen_dir.is_symlink():
        raise ValueError(f"Generation path is not a regular directory: {gen_dir}")

    archive_sha12 = expected_sha[:12]
    expected_gen_name = f"v{expected_version}-{expected_mode}-{archive_sha12}"
    if gen_dir.name != expected_gen_name:
        raise ValueError(f"Generation directory name mismatch: expected '{expected_gen_name}', got '{gen_dir.name}'")

    zip_versioned = f"ComfyUI-IYKYK-v{expected_version}.zip"
    zip_latest = "ComfyUI-IYKYK-latest.zip"
    zip_generic = "ComfyUI-IYKYK.zip"

    expected_files = {
        zip_versioned,
        zip_latest,
        zip_generic,
        "SHA256SUMS.txt",
        "MANIFEST.json",
    }

    # 1. 严格目录条目级白名单：拒绝子目录、符号链接与非普通文件
    all_entries = list(gen_dir.iterdir())
    for entry in all_entries:
        if entry.is_symlink():
            raise ValueError(f"Forbidden symlink detected in generation directory: {entry.name}")
        if entry.is_dir():
            raise ValueError(f"Forbidden subdirectory detected in generation directory: {entry.name}")
        if not entry.is_file():
            raise ValueError(f"Non-regular file entry detected in generation directory: {entry.name}")

    actual_files = set(entry.name for entry in all_entries)
    if actual_files != expected_files:
        missing = expected_files - actual_files
        extra = actual_files - expected_files
        raise ValueError(
            f"Generation directory violates strict file whitelist! Missing: {missing}, Extra: {extra}"
        )

    # 2. 检查 3 个 ZIP 文件的实际 SHA256
    for zip_name in (zip_versioned, zip_latest, zip_generic):
        zip_path = gen_dir / zip_name
        actual_hash = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if actual_hash != expected_sha:
            raise ValueError(
                f"ZIP {zip_name} hash on disk ({actual_hash}) does not match expected ({expected_sha})"
            )

    # 3. 严格校验 SHA256SUMS.txt
    sums_file = gen_dir / "SHA256SUMS.txt"
    sums_content = sums_file.read_text(encoding="utf-8")
    expected_sums = (
        f"{expected_sha}  {zip_versioned}\n"
        f"{expected_sha}  {zip_latest}\n"
        f"{expected_sha}  {zip_generic}\n"
    )
    if sums_content != expected_sums:
        raise ValueError(
            f"SHA256SUMS.txt does not match exact 3-line format and hashes! Got:\n{sums_content!r}"
        )

    # 4. 严格校验 MANIFEST.json
    manifest_file = gen_dir / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"MANIFEST.json is corrupted or not valid JSON: {e}") from e

    if not isinstance(manifest, dict):
        raise ValueError(f"MANIFEST.json must be a JSON object, got {type(manifest).__name__}")

    actual_keys = set(manifest.keys())
    if actual_keys != EXPECTED_MANIFEST_KEYS:
        missing_k = EXPECTED_MANIFEST_KEYS - actual_keys
        extra_k = actual_keys - EXPECTED_MANIFEST_KEYS
        raise ValueError(f"MANIFEST.json fields mismatch! Missing: {missing_k}, Extra: {extra_k}")

    if manifest.get("extension_name") != "ComfyUI-IYKYK":
        raise ValueError(f"Manifest extension_name mismatch: {manifest.get('extension_name')}")
    if manifest.get("version") != f"v{expected_version}":
        raise ValueError(f"Manifest version mismatch: expected 'v{expected_version}', got '{manifest.get('version')}'")
    if manifest.get("mode") != expected_mode:
        raise ValueError(f"Manifest mode mismatch: expected '{expected_mode}', got '{manifest.get('mode')}'")
    if expected_source_commit is not None and manifest.get("source_commit") != expected_source_commit:
        raise ValueError(
            f"Manifest source_commit mismatch: expected '{expected_source_commit}', got '{manifest.get('source_commit')}'"
        )
    if not manifest.get("source_commit"):
        raise ValueError("Manifest source_commit cannot be empty")
    if manifest.get("release_file") != zip_versioned:
        raise ValueError(f"Manifest release_file mismatch: expected '{zip_versioned}', got '{manifest.get('release_file')}'")
    if manifest.get("sha256") != expected_sha:
        raise ValueError(f"Manifest sha256 mismatch: expected '{expected_sha}', got '{manifest.get('sha256')}'")

    m_files = manifest.get("files")
    m_files_sha = manifest.get("files_sha256")
    m_count = manifest.get("files_count")

    if not isinstance(m_files, list) or not isinstance(m_files_sha, dict) or not isinstance(m_count, int):
        raise ValueError("Invalid manifest types for files, files_sha256, or files_count")

    if m_count != len(m_files) or m_count != len(m_files_sha):
        raise ValueError(
            f"Manifest files_count ({m_count}) mismatch with len(files)={len(m_files)} or len(files_sha256)={len(m_files_sha)}"
        )

    if len(m_files) != len(set(m_files)):
        raise ValueError("Duplicate paths detected in manifest files list")

    if set(m_files) != set(m_files_sha.keys()):
        raise ValueError("Manifest files list does not match keys of files_sha256")

    for fpath in m_files:
        if not isinstance(fpath, str):
            raise ValueError(f"Non-string path in manifest files: {fpath!r}")
        if fpath.startswith("/") or fpath.startswith("\\") or (len(fpath) > 1 and fpath[1] == ":"):
            raise ValueError(f"Forbidden absolute path in manifest: {fpath}")
        p_parts = Path(fpath).parts
        if ".." in p_parts:
            raise ValueError(f"Forbidden '..' path traversal in manifest: {fpath}")
        if not fpath.startswith("ComfyUI-IYKYK/"):
            raise ValueError(f"Path does not start with ComfyUI-IYKYK/: {fpath}")

    if expected_files_sha is not None:
        if m_files_sha != expected_files_sha:
            raise ValueError("Manifest files_sha256 does not match expected_files_sha")

    # 5. 深度校验 ZIP 包内实物：testzip() 与 38 个文件的实际内容 SHA256 逐一比对 (R3)
    for zip_name in (zip_versioned, zip_latest, zip_generic):
        zip_p = gen_dir / zip_name
        try:
            validate_zip_entry_integrity(zip_p, m_files_sha)
        except Exception as e:
            raise ValueError(f"Failed to verify package integrity of {zip_name}: {e}") from e


INCLUDED_ROOT_FILES = [
    "__init__.py",
    "nodes.py",
    "pyproject.toml",
    ".comfyignore",
    "README.md",
    "CHANGELOG.md",
]


def get_project_version(repo_dir: Path) -> str:
    pyproject = repo_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise FileNotFoundError(f"Missing pyproject.toml in {repo_dir}")
    content = pyproject.read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', content)
    if not m:
        raise ValueError("Could not find project.version in pyproject.toml")
    return m.group(1)


def verify_frontend_version(repo_dir: Path, version: str):
    version_file = repo_dir / "js" / "version.js"
    if not version_file.is_file():
        raise FileNotFoundError(f"Missing {version_file}! Please run scripts/sync_version.py to generate it.")
    expected_content = f'export const EXTENSION_VERSION = "v{version}";\n'
    actual_content = version_file.read_text(encoding="utf-8")
    if actual_content != expected_content:
        raise ValueError(
            f"Version mismatch! pyproject.toml has '{version}', but js/version.js has '{actual_content.strip()}'. "
            "Build does not modify source files. Run `python3 scripts/sync_version.py` to sync version manually."
        )


def check_clean_git_tree(repo_dir: Path):
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        capture_output=True,
        text=True
    )
    if res.returncode != 0:
        raise RuntimeError(f"Git command failed in {repo_dir}: {res.stderr.strip()}")
    if res.stdout.strip():
        raise RuntimeError(
            f"Working tree at {repo_dir} is dirty! Release packaging requires a clean tree.\n"
            f"Uncommitted changes:\n{res.stdout.strip()}"
        )


def get_git_metadata(repo_dir: Path, version: str, mode: str) -> tuple[str, int, str]:
    # 1. 获取 HEAD commit
    res_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True
    )
    if res_commit.returncode != 0:
        raise RuntimeError(f"Failed to get HEAD commit in {repo_dir}: {res_commit.stderr.strip()}")
    source_commit = res_commit.stdout.strip()

    # 2. 获取 HEAD timestamp (或优先使用 SOURCE_DATE_EPOCH)
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    if sde is not None:
        try:
            raw_ts = int(sde.strip())
        except ValueError:
            raise ValueError(f"Invalid non-integer SOURCE_DATE_EPOCH: {sde!r}")
        commit_ts, _, iso_timestamp = validate_and_align_timestamp(raw_ts)
    else:
        res_time = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        if res_time.returncode != 0:
            raise RuntimeError(f"Failed to get HEAD commit timestamp in {repo_dir}: {res_time.stderr.strip()}")
        raw_ts = int(res_time.stdout.strip())
        commit_ts, _, iso_timestamp = validate_and_align_timestamp(raw_ts)

    # 3. 如果是 release 模式，必须严格校验 annotated tag
    if mode == "release":
        expected_tag = f"v{version}"
        res_tag = subprocess.run(
            ["git", "tag", "--points-at", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        if res_tag.returncode != 0:
            raise RuntimeError(f"Failed to check git tags in {repo_dir}: {res_tag.stderr.strip()}")
        tags = [t.strip() for t in res_tag.stdout.splitlines() if t.strip()]
        if expected_tag not in tags:
            raise RuntimeError(
                f"Release mode requires annotated tag '{expected_tag}' pointing at HEAD commit {source_commit[:8]}, "
                f"but found tags pointing at HEAD: {tags}"
            )
        # 校验必须为 annotated tag (git cat-file -t 返回 'tag')
        res_type = subprocess.run(
            ["git", "cat-file", "-t", expected_tag],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        tag_type = res_type.stdout.strip() if res_type.returncode == 0 else "unknown"
        if res_type.returncode != 0 or tag_type != "tag":
            raise RuntimeError(
                f"Tag '{expected_tag}' is a lightweight tag ({tag_type})! "
                "Release mode requires an annotated tag (`git tag -a`)."
            )

    return source_commit, commit_ts, iso_timestamp


def run_data_validation(repo_dir: Path):
    print("🔍 Step 1: Running dataset schema and integrity validation...")
    val_script = repo_dir / "scripts" / "validate_data.py"
    res = subprocess.run([sys.executable, str(val_script), "--strict"], cwd=repo_dir, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr)
        raise RuntimeError("Data validation failed! Release packaging aborted.")
    print("   ✅ Dataset validation passed with 0 errors.")


def validate_source_file_safety(repo_dir: Path, rel_path_str: str) -> Path:
    """严格校验源文件路径安全性：规范相对路径、无符号链接、无路径穿越且位于 repo_root 内 (P1-1)。

    遵循 macOS 兼容原则：
    1. repo_root = repo_dir.resolve(strict=True) 消除 /var -> /private/var 前缀别名
    2. 从 repo_root 拼接相对路径，逐级 os.lstat() 断言绝非符号链接
    3. 目标文件必须为普通文件 (stat.S_ISREG)
    4. resolve(strict=True) 结果必须在 repo_root 内部且与拼接路径一致
    """
    if not isinstance(rel_path_str, str) or "\\" in rel_path_str:
        raise ValueError(f"Invalid path format: {rel_path_str!r}")
    p = Path(rel_path_str)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"Non-normalized path: {rel_path_str!r}")

    repo_root = repo_dir.resolve(strict=True)
    curr = repo_root
    for part in p.parts:
        curr = curr / part
        if not curr.exists() and not curr.is_symlink():
            raise FileNotFoundError(f"Missing path component: {curr} (rel: {rel_path_str})")
        st = os.lstat(curr)
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(f"Forbidden symlink detected at path component: {curr} (rel: {rel_path_str})")

    st = os.lstat(curr)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Not a regular file: {curr} (rel: {rel_path_str})")

    resolved = curr.resolve(strict=True)
    if resolved != curr:
        raise ValueError(f"Path drift: resolved {resolved} != direct {curr}")

    try:
        resolved.relative_to(repo_root)
    except ValueError:
        raise ValueError(f"Path escaped repo root: {resolved} outside {repo_root}")

    return curr


def collect_files_to_pack(repo_dir: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []

    # 路径规范校验：杜绝清单自身重复
    if len(RUNTIME_PACKAGE_FILES) != len(set(RUNTIME_PACKAGE_FILES)):
        raise ValueError("Duplicate paths detected in RUNTIME_PACKAGE_FILES manifest")

    # 严格根据单一事实来源 RUNTIME_PACKAGE_FILES 收集并在读取前做安全路径校验 (P1-1)
    for rel_path in RUNTIME_PACKAGE_FILES:
        safe_fp = validate_source_file_safety(repo_dir, rel_path)
        files.append((safe_fp, f"ComfyUI-IYKYK/{rel_path}"))

    # 源树严格门禁：检查 lib/ 和 js/ 是否存在未在 manifest 登记的 .py / .js (P1-5)
    lib_dir = repo_dir / "lib"
    if lib_dir.is_dir():
        for p in lib_dir.glob("*.py"):
            rel = f"lib/{p.name}"
            if rel not in RUNTIME_PACKAGE_FILES:
                raise RuntimeError(f"Strict source tree gate failed: untracked runtime candidate file found: {rel}")

    js_dir = repo_dir / "js"
    if js_dir.is_dir():
        for p in js_dir.glob("*.js"):
            rel = f"js/{p.name}"
            if rel not in RUNTIME_PACKAGE_FILES:
                raise RuntimeError(f"Strict source tree gate failed: untracked runtime candidate file found: {rel}")

    if len(files) != TOTAL_RUNTIME_FILES_COUNT:
        raise ValueError(
            f"Files count mismatch: expected {TOTAL_RUNTIME_FILES_COUNT}, got {len(files)}"
        )

    # 按包内相对路径严格排序 (确保确定性)
    files.sort(key=lambda x: x[1])
    return files


def write_deterministic_zip(files: list[tuple[Path, str]], output_path: Path, zip_ts: tuple[int, int, int, int, int, int]):
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full_path, arc_name in files:
            data = full_path.read_bytes()
            zinfo = zipfile.ZipInfo(filename=arc_name, date_time=zip_ts)
            zinfo.create_system = 3  # Unix (R3)
            zinfo.external_attr = (stat.S_IFREG | 0o644) << 16  # standard regular file permissions
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zinfo, data)


def smoke_test_package(zip_path: Path):
    print("🧪 Step 3: Running isolated smoke test on generated package...")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        test_dir = (Path(tmp) / "ComfyUI-IYKYK").resolve()
        script = f"""
import sys
import os
import json
import importlib.util
from pathlib import Path

test_dir = Path(r'{test_dir}').resolve()
repo_dir_str = r'{REPO_DIR.resolve()}'

# 1. 显式从 sys.path 中清理任何工作区目录、editable install 路径与 test_dir
cleaned_sys_path = []
for p in list(sys.path):
    resolved_p = str(Path(p).resolve()) if p else ""
    if resolved_p == str(test_dir):
        continue
    if resolved_p and resolved_p.startswith(repo_dir_str):
        continue
    if "ComfyUI-IYKYK" in resolved_p or "comfyui_iykyk" in resolved_p:
        continue
    cleaned_sys_path.append(p)
sys.path = cleaned_sys_path

# 断言 test_dir 绝不在 sys.path 中 (R2)
assert str(test_dir) not in [str(Path(p).resolve()) if p else "" for p in sys.path], "test_dir must NOT be in sys.path"

# 2. 使用 package-style spec_from_file_location 加载解压包根 __init__.py
init_file = test_dir / "__init__.py"
assert init_file.is_file(), f"Missing __init__.py at {{init_file}}"
spec = importlib.util.spec_from_file_location(
    "ComfyUI_IYKYK",
    str(init_file),
    submodule_search_locations=[str(test_dir)]
)
assert spec and spec.loader, "Failed to create module spec for ComfyUI_IYKYK"
pkg = importlib.util.module_from_spec(spec)
sys.modules["ComfyUI_IYKYK"] = pkg
spec.loader.exec_module(pkg)

# 获取 nodes 模块
nodes = sys.modules.get("ComfyUI_IYKYK.nodes")
assert nodes is not None, "Failed to load ComfyUI_IYKYK.nodes"

# 3. 验证没有从任何包外源码目录加载模块 (Path.resolve().relative_to())
for mod_name, mod in list(sys.modules.items()):
    if mod and hasattr(mod, '__file__') and mod.__file__:
        if mod_name.startswith('ComfyUI_IYKYK') or mod_name.startswith('nodes') or mod_name.startswith('lib'):
            mod_path = Path(mod.__file__).resolve()
            try:
                mod_path.relative_to(test_dir)
            except ValueError:
                raise AssertionError(f"External module leak: {{mod_name}} loaded from {{mod_path}} outside {{test_dir}}")

# 4. 验证 19 个运行时数据文件全部从包内成功解析
data_dir = test_dir / 'data'
from ComfyUI_IYKYK.lib.runtime_manifest import RUNTIME_DATA_FILES
for df in RUNTIME_DATA_FILES:
    p = data_dir / df
    assert p.is_file(), f"Missing packaged data file {{df}}"
    d = json.loads(p.read_text(encoding='utf-8'))
    assert isinstance(d, dict), f"Invalid json in {{df}}"

# 5. 验证 3 个节点的 INPUT_TYPES
for name, cls in nodes.NODE_CLASS_MAPPINGS.items():
    types = cls.INPUT_TYPES()
    assert isinstance(types, dict), f"INPUT_TYPES failed for {{name}}"

# 6. 验证 3 个节点的 IS_CHANGED
h1 = nodes.IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42)
h2 = nodes.IYKYKPresetBrowser.IS_CHANGED(prompt_seed=42)
h3 = nodes.IYKYKCustomSlotCombiner.IS_CHANGED(prompt_seed=42)
assert isinstance(h1, str) and len(h1) == 64
assert isinstance(h2, str) and len(h2) == 64
assert isinstance(h3, str) and len(h3) == 64

# 7. 验证主生成器
gen = nodes.IYKYKPromptGenerator()
for lvl in ['L2 差分微露 (Partially Exposed)', 'L3 半裸诱惑 (Half Nude)', 'L4 重点暴露 (Topless / Bottomless)']:
    pos, neg, desc = gen.generate(
        预设模板='无 (None)',
        风格配方='无 (None)',
        场景大类='随机 (Random)',
        剧情主题='随机 (Random)',
        景别构图='自动 (Auto)',
        拍摄视角='自动 (Auto)',
        裸露等级=lvl,
        服装款式='随机 (Random)',
        服装状态='自动联动裸露等级 (Auto Link Nudity)',
        发型发色='随机 (Random)',
        饰品头饰='无 (None)',
        妆容细节='无 (None)',
        姿势动作='随机 (Random)',
        情绪表情='随机 (Random)',
        光影预设='自动 (Auto)',
        胶片风格='无 (None)',
        液体效果='无 (None)',
        纹身标记='无 (None)',
        道具物件='无 (None)',
        角色设定='无 (None)',
        真实微瑕='无 (None)',
        画质等级='高清写真 (High)',
        prompt_seed=42
    )
    assert len(pos) > 0, f"Positive prompt empty for {{lvl}}"

# 8. 验证预设浏览节点 (含 browse 与 browse_structured)
browser = nodes.IYKYKPresetBrowser()
first_preset = nodes._sampler.list_preset_names()[0]
b_pos, b_neg, b_desc = browser.browse(first_preset, '无 (None)', '高清写真 (High)', prompt_seed=42)
assert len(b_pos) > 0
b_res = browser.browse_structured(first_preset, '无 (None)', '高清写真 (High)', prompt_seed=42)
assert len(b_res.positive) > 0 and len(b_res.source_atoms) > 0

# 9. 验证自定义组合节点 (含 combine 与 combine_structured — 重点验证 P1-1)
comb = nodes.IYKYKCustomSlotCombiner()
c_pos, c_neg, c_desc = comb.combine(prompt_seed=42, 场景主题='onsen')
assert len(c_pos) > 0
c_res = comb.combine_structured(prompt_seed=42, 场景主题='onsen')
assert len(c_res.positive) > 0 and len(c_res.source_atoms) > 0

print('   ✅ Package generation smoke test passed for all 3 nodes (including combine_structured).')
"""
        isolated_env = {"PATH": os.environ.get("PATH", "")}
        res = subprocess.run([sys.executable, "-I", "-c", script], cwd=str(test_dir), env=isolated_env, capture_output=True, text=True)
        if res.returncode != 0:
            print(res.stdout)
            print(res.stderr)
            raise RuntimeError("Smoke test on packaged release failed!")
        print(res.stdout.strip())


def build_release(repo_dir: Path, output_dir: Path, mode: str = "verify"):
    # 0. 检查 clean working tree (无任何绕过参数)
    check_clean_git_tree(repo_dir)

    version = get_project_version(repo_dir)
    verify_frontend_version(repo_dir, version)
    source_commit, commit_ts, iso_timestamp = get_git_metadata(repo_dir, version, mode)

    _, zip_ts, iso_timestamp = validate_and_align_timestamp(commit_ts)

    print(f"🚀 Building ComfyUI-IYKYK Release v{version} [mode={mode}] (commit: {source_commit[:8]}, date: {iso_timestamp})...")

    # 1. 强校验数据
    run_data_validation(repo_dir)

    # 2. 收集并校验 38 个运行时文件
    files = collect_files_to_pack(repo_dir)
    print(f"📦 Step 2: Collected {len(files)} runtime files for packaging.")

    # 计算每个包内文件的 SHA256
    files_sha256: dict[str, str] = {}
    for full_path, arc_name in files:
        files_sha256[arc_name] = hashlib.sha256(full_path.read_bytes()).hexdigest()

    output_dir.mkdir(parents=True, exist_ok=True)
    build_uuid = uuid.uuid4().hex[:8]
    tmp_gen_dir = output_dir / f".tmp_gen_{version}_{os.getpid()}_{build_uuid}"
    if tmp_gen_dir.exists():
        shutil.rmtree(tmp_gen_dir)
    tmp_gen_dir.mkdir(parents=True, exist_ok=True)
    tmp_pointer: Optional[Path] = None

    try:
        # 3. 在独立 generation 临时目录构建 3 个完全同构的 ZIP 产物
        zip_versioned_name = f"ComfyUI-IYKYK-v{version}.zip"
        zip_latest_name = "ComfyUI-IYKYK-latest.zip"
        zip_generic_name = "ComfyUI-IYKYK.zip"

        staging_versioned_zip = tmp_gen_dir / zip_versioned_name
        write_deterministic_zip(files, staging_versioned_zip, zip_ts)
        sha256_val = hashlib.sha256(staging_versioned_zip.read_bytes()).hexdigest()

        staging_latest_zip = tmp_gen_dir / zip_latest_name
        shutil.copyfile(staging_versioned_zip, staging_latest_zip)

        staging_generic_zip = tmp_gen_dir / zip_generic_name
        shutil.copyfile(staging_versioned_zip, staging_generic_zip)

        # 4. 执行隔离环境烟雾测试
        smoke_test_package(staging_versioned_zip)

        # 5. 生成 SHA256SUMS.txt
        staging_sums = tmp_gen_dir / "SHA256SUMS.txt"
        sums_content = (
            f"{sha256_val}  {zip_versioned_name}\n"
            f"{sha256_val}  {zip_latest_name}\n"
            f"{sha256_val}  {zip_generic_name}\n"
        )
        staging_sums.write_text(sums_content, encoding="utf-8")

        # 6. 生成 MANIFEST.json
        staging_manifest = tmp_gen_dir / "MANIFEST.json"
        manifest_data = {
            "extension_name": "ComfyUI-IYKYK",
            "version": f"v{version}",
            "mode": mode,
            "source_commit": source_commit,
            "generated_at": iso_timestamp,
            "release_file": zip_versioned_name,
            "sha256": sha256_val,
            "files_count": len(files),
            "files": [arc_name for _, arc_name in files],
            "files_sha256": files_sha256,
        }
        staging_manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 7. 全量文件 fsync 刷盘保证物理持久化
        for p in [staging_versioned_zip, staging_latest_zip, staging_generic_zip, staging_sums, staging_manifest]:
            with open(p, "rb") as f:
                os.fsync(f.fileno())

        # 8. 严格状态机：获取发布锁执行目标 generation 提交与唯一原子指针更新
        archive_sha12 = sha256_val[:12]
        target_gen_name = f"v{version}-{mode}-{archive_sha12}"
        target_gen_dir = output_dir / target_gen_name

        with ReleaseLock(output_dir / ".build.lock"):
            # 锁内重新读取并验证目标 generation 与当前指针状态
            if target_gen_dir.exists():
                # 目标已存在：执行完整 Fail-Closed 幂等校验，任何残缺、多余或不符立即终止报错
                validate_generation_integrity(
                    target_gen_dir,
                    expected_version=version,
                    expected_sha=sha256_val,
                    expected_mode=mode,
                    expected_source_commit=source_commit,
                    expected_files_sha=files_sha256,
                )
                print(f"   ℹ️ Target generation {target_gen_name} already exists with identical content (Idempotent pass).")
            else:
                # 目标不存在：原子提交 generation 目录并刷新父目录
                os.replace(tmp_gen_dir, target_gen_dir)
                fsync_dir(output_dir)

            # 再次验证已提交的目标 generation 实物完整性 (双重校验)
            validate_generation_integrity(
                target_gen_dir,
                expected_version=version,
                expected_sha=sha256_val,
                expected_mode=mode,
                expected_source_commit=source_commit,
                expected_files_sha=files_sha256,
            )

            # 9. 唯一原子指针更新 (发布事务最后持久化操作)
            pointer_data = {
                "version": f"v{version}",
                "mode": mode,
                "generation_dir": target_gen_name,
                "release_file": zip_versioned_name,
                "sha256": sha256_val,
                "generated_at": iso_timestamp,
                "source_commit": source_commit,
            }
            tmp_pointer = output_dir / f".CURRENT.json.tmp_{build_uuid}"
            tmp_pointer.write_text(json.dumps(pointer_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with open(tmp_pointer, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp_pointer, output_dir / "CURRENT.json")
            tmp_pointer = None
            fsync_dir(output_dir)

        print("\n🎉 Build Completed Successfully!")
        print(f"   Mode:            {mode}")
        print(f"   Commit:          {source_commit}")
        print(f"   Output dir:      {output_dir}")
        print(f"   Generation dir:  {target_gen_dir}")
        print(f"   Release ZIP:     {target_gen_dir / zip_versioned_name}")
        print(f"   Files count:     {len(files)}")
        print(f"   SHA256:          {sha256_val}")
        print(f"   Active Pointer:  {output_dir / 'CURRENT.json'}\n")

    finally:
        if tmp_gen_dir.exists():
            shutil.rmtree(tmp_gen_dir, ignore_errors=True)
        if tmp_pointer is not None and tmp_pointer.exists():
            try:
                tmp_pointer.unlink()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Deterministic release packaging for ComfyUI-IYKYK.")
    parser.add_argument("--mode", choices=["verify", "release"], default="verify", help="Build mode (verify or release)")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to output release zip and manifest")
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else REPO_DIR.parent
    build_release(REPO_DIR, out_dir, mode=args.mode)


if __name__ == "__main__":
    main()
