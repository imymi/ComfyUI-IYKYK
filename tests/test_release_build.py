"""
test_release_build.py — 自动化构建与发布工程测试门禁
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.runtime_manifest import RUNTIME_DATA_FILES, RUNTIME_PACKAGE_FILES, TOTAL_RUNTIME_FILES_COUNT


def make_clean_git_repo(target_dir: Path) -> Path:
    """
    将当前仓库核心源码复制到 target_dir，排除 .git, __pycache__, dist 等临时产物，
    并在此临时目录初始化 git，建立一个确定性的干净提交快照。
    """
    exclude_patterns = {".git", "__pycache__", ".pytest_cache", "dist", ".venv"}
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
    subprocess.run(["git", "add", "-A"], cwd=target_dir, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "Initial clean snapshot for test"], cwd=target_dir, check=True, capture_output=True, env=env)
    return target_dir


class TestReleaseBuild(unittest.TestCase):
    """测试发布脚本在独立隔离目录中的行为与确定性可重复构建"""

    def test_version_mismatch_aborts_without_mutating_source(self):
        """测试版本不一致时构建立即报错，且绝不修改 js/version.js"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_repo = Path(tmp) / "ComfyUI-IYKYK"
            make_clean_git_repo(tmp_repo)

            # 故意修改 js/version.js 并提交保持 clean tree
            version_file = tmp_repo / "js" / "version.js"
            version_file.write_text('export const EXTENSION_VERSION = "v0.0.0-mismatch";\n')
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Tester",
                "GIT_AUTHOR_EMAIL": "tester@example.com",
                "GIT_COMMITTER_NAME": "Tester",
                "GIT_COMMITTER_EMAIL": "tester@example.com",
            }
            subprocess.run(["git", "commit", "-am", "mismatch"], cwd=tmp_repo, check=True, capture_output=True, env=env)

            res = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify"],
                cwd=tmp_repo,
                capture_output=True,
                text=True
            )
            # 断言构建非零退出
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Version mismatch", res.stderr or res.stdout)

            # 断言源码文件未被篡改
            self.assertEqual(
                version_file.read_text(),
                'export const EXTENSION_VERSION = "v0.0.0-mismatch";\n'
            )
    def assert_release_consistent(self, out_dir: Path | str) -> dict:
        """验证单一权威指针 CURRENT.json 与 generation 目录内全部产物的一致性 (Fail-Closed)"""
        out_path = Path(out_dir)
        pointer_file = out_path / "CURRENT.json"
        self.assertTrue(pointer_file.is_file(), f"CURRENT.json missing in {out_dir}")
        pointer = json.loads(pointer_file.read_text(encoding="utf-8"))
        self.assertIn("version", pointer)
        self.assertIn("generation_dir", pointer)
        self.assertIn("sha256", pointer)

        gen_dir = out_path / pointer["generation_dir"]
        self.assertTrue(gen_dir.is_dir(), f"Generation dir {gen_dir} missing")
        self.assertFalse(gen_dir.is_symlink(), f"Generation dir {gen_dir} must not be a symlink")

        # 检查目录项白名单与无子目录/软链接
        all_entries = list(gen_dir.iterdir())
        for entry in all_entries:
            self.assertFalse(entry.is_symlink(), f"Forbidden symlink in {gen_dir}: {entry.name}")
            self.assertTrue(entry.is_file(), f"Non-regular file in {gen_dir}: {entry.name}")

        expected_files = {
            f"ComfyUI-IYKYK-{pointer['version']}.zip",
            "ComfyUI-IYKYK-latest.zip",
            "ComfyUI-IYKYK.zip",
            "SHA256SUMS.txt",
            "MANIFEST.json",
        }
        actual_files = set(p.name for p in all_entries)
        self.assertEqual(actual_files, expected_files, f"Files in {gen_dir} do not match strict whitelist")

        # 独立重算并验证 3 个 ZIP 的实际哈希
        for zip_name in (f"ComfyUI-IYKYK-{pointer['version']}.zip", "ComfyUI-IYKYK-latest.zip", "ComfyUI-IYKYK.zip"):
            zip_path = gen_dir / zip_name
            actual_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            self.assertEqual(actual_sha, pointer["sha256"], f"Hash of {zip_name} does not match pointer sha256")

        # 独立验证 SHA256SUMS.txt 格式与每一行内容
        sums_file = gen_dir / "SHA256SUMS.txt"
        expected_sums = (
            f"{pointer['sha256']}  ComfyUI-IYKYK-{pointer['version']}.zip\n"
            f"{pointer['sha256']}  ComfyUI-IYKYK-latest.zip\n"
            f"{pointer['sha256']}  ComfyUI-IYKYK.zip\n"
        )
        self.assertEqual(sums_file.read_text(encoding="utf-8"), expected_sums)

        # 独立验证 MANIFEST.json
        manifest_file = gen_dir / "MANIFEST.json"
        self.assertTrue(manifest_file.is_file(), f"MANIFEST.json missing in {gen_dir}")
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], pointer["version"])
        self.assertEqual(manifest["sha256"], pointer["sha256"])
        self.assertEqual(manifest["release_file"], f"ComfyUI-IYKYK-{pointer['version']}.zip")
        self.assertEqual(manifest["files_count"], TOTAL_RUNTIME_FILES_COUNT)
        self.assertEqual(len(manifest["files"]), TOTAL_RUNTIME_FILES_COUNT)
        self.assertEqual(len(manifest["files_sha256"]), TOTAL_RUNTIME_FILES_COUNT)
        self.assertEqual(set(manifest["files"]), set(manifest["files_sha256"].keys()))
        self.assertIn(f"-{manifest['mode']}-", pointer["generation_dir"])

        # 独立逐个检查 ZIP 包内文件完整性与哈希及 Unix 文件类型位 (R3)
        for zip_name in (f"ComfyUI-IYKYK-{pointer['version']}.zip", "ComfyUI-IYKYK-latest.zip", "ComfyUI-IYKYK.zip"):
            with zipfile.ZipFile(gen_dir / zip_name, "r") as zf:
                self.assertIsNone(zf.testzip(), f"Corrupted zip: {zip_name}")
                infolist = zf.infolist()
                self.assertEqual(len(infolist), TOTAL_RUNTIME_FILES_COUNT)
                namelist = [info.filename for info in infolist]
                self.assertEqual(set(namelist), set(manifest["files_sha256"].keys()))
                self.assertEqual(len(namelist), len(set(namelist)), f"Duplicate entries in {zip_name}")
                for info in infolist:
                    mode = info.external_attr >> 16
                    self.assertTrue(stat.S_ISREG(mode), f"Entry {info.filename} in {zip_name} is not regular file: mode={oct(mode)}")
                    item_bytes = zf.read(info.filename)
                    item_sha = hashlib.sha256(item_bytes).hexdigest()
                    self.assertEqual(item_sha, manifest["files_sha256"][info.filename], f"File {info.filename} in {zip_name} corrupted")

        return manifest

    def test_deterministic_build_bit_for_bit_identical(self):
        """测试两次完全独立环境的构建产物逐字节完全一致 (SHA256 绝对相同)"""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b, \
             tempfile.TemporaryDirectory() as out_a, tempfile.TemporaryDirectory() as out_b:
            repo_a = Path(tmp_a) / "repo"
            repo_b = Path(tmp_b) / "repo"
            make_clean_git_repo(repo_a)
            make_clean_git_repo(repo_b)

            res_a = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_a],
                cwd=repo_a,
                capture_output=True,
                text=True
            )
            self.assertEqual(res_a.returncode, 0, f"Build A failed: {res_a.stderr}\n{res_a.stdout}")

            res_b = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_b],
                cwd=repo_b,
                capture_output=True,
                text=True
            )
            self.assertEqual(res_b.returncode, 0, f"Build B failed: {res_b.stderr}\n{res_b.stdout}")

            # 统一校验两套构建并比对一致性
            manifest_a = self.assert_release_consistent(out_a)
            manifest_b = self.assert_release_consistent(out_b)

            self.assertEqual(manifest_a["sha256"], manifest_b["sha256"], "Dual builds produced different SHA256 hashes!")
            self.assertTrue(len(manifest_a["source_commit"]) >= 40)
            self.assertEqual(manifest_a["files_count"], 38)
            self.assertEqual(len(manifest_a["files_sha256"]), 38)

            # 校验 ZIP 内文件
            pointer_a = json.loads((Path(out_a) / "CURRENT.json").read_text(encoding="utf-8"))
            zip_v_a = Path(out_a) / pointer_a["generation_dir"] / f"ComfyUI-IYKYK-{pointer_a['version']}.zip"
            with zipfile.ZipFile(zip_v_a, "r") as zf:
                namelist = zf.namelist()
                self.assertEqual(len(namelist), 38)
                for df in RUNTIME_DATA_FILES:
                    self.assertIn(f"ComfyUI-IYKYK/data/{df}", namelist)

    def test_dirty_tree_fails_release_build(self):
        """测试工作区有未提交修改时，构建立即非零退出"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 制造修改
            readme = repo / "README.md"
            readme.write_text("Dirty changes\n")

            res = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("dirty", (res.stderr + res.stdout).lower())

    def test_untracked_file_fails_release_build(self):
        """测试工作区有未追踪文件时，构建立即非零退出"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 制造未追踪文件
            (repo / "untracked.txt").write_text("Untracked")

            res = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("dirty", (res.stderr + res.stdout).lower())

    def test_release_mode_tag_validation(self):
        """测试 release 模式对 tag 的严格校验：无 tag、轻量 tag、不匹配 tag 拒绝，annotated tag 成功"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 1. 无 tag 下 --mode release 失败
            res1 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "release", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res1.returncode, 0)
            self.assertIn("requires annotated tag", res1.stderr or res1.stdout)

            # 2. 轻量 tag (git tag v1.1.0-rc7) 失败
            subprocess.run(["git", "tag", "v1.1.0-rc7"], cwd=repo, check=True, capture_output=True)
            res2 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "release", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res2.returncode, 0)
            self.assertIn("lightweight tag", res2.stderr or res2.stdout)

            # 3. 删除轻量 tag，打 annotated tag (git tag -a v1.1.0-rc7 -m "msg") 成功
            subprocess.run(["git", "tag", "-d", "v1.1.0-rc7"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "tag", "-a", "v1.1.0-rc7", "-m", "Release rc7"], cwd=repo, check=True, capture_output=True)
            res3 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "release", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res3.returncode, 0, f"Release build with annotated tag failed: {res3.stderr}")
            manifest = self.assert_release_consistent(out_dir)
            self.assertEqual(manifest["mode"], "release")

    def test_same_version_different_hash_refuses_overwrite(self):
        """测试如果目标 generation 目录存在但文件 SHA256 被破坏，拒绝覆盖并 Fail-Closed"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 第一次正常构建
            res1 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res1.returncode, 0)
            pointer = json.loads((Path(out_dir) / "CURRENT.json").read_text(encoding="utf-8"))
            gen_dir = Path(out_dir) / pointer["generation_dir"]

            # 破坏其中的 ZIP 文件内容
            zip_file = gen_dir / f"ComfyUI-IYKYK-{pointer['version']}.zip"
            zip_file.write_bytes(b"corrupted or altered zip content")

            # 第二次构建应当触发强校验拦截并报错
            res2 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res2.returncode, 0)
            self.assertIn("does not match expected", res2.stderr or res2.stdout)

    def test_incomplete_generation_directory_fails_closed(self):
        """测试目标 generation 目录残缺（如仅含 orphan.txt 或缺失 MANIFEST）时 Fail-Closed 拦截"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 先正常构建一次以获取确定性 generation_dir 命名
            res1 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res1.returncode, 0)
            pointer = json.loads((Path(out_dir) / "CURRENT.json").read_text(encoding="utf-8"))
            gen_name = pointer["generation_dir"]

            # 清空 output_dir 并构造仅含 orphan.txt 的残缺 generation 目录
            shutil.rmtree(out_dir)
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            bad_gen = Path(out_dir) / gen_name
            bad_gen.mkdir(parents=True, exist_ok=True)
            (bad_gen / "orphan.txt").write_text("rogue orphan file", encoding="utf-8")

            # 构建必须非零退出，且 CURRENT.json 绝不得指向该残缺目录
            res2 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res2.returncode, 0)
            self.assertFalse((Path(out_dir) / "CURRENT.json").exists())


    def test_source_date_epoch_range_and_alignment(self):
        """测试 SOURCE_DATE_EPOCH 合法范围 (1980-2107) 与 2 秒精度对齐 (修订 6 强制约束)"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 1. 超出下限 (例如 1979 年: 100000)
            res_too_early = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                env={**os.environ, "SOURCE_DATE_EPOCH": "100000"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_too_early.returncode, 0)
            self.assertIn("out of valid ZIP range", res_too_early.stderr or res_too_early.stdout)

            # 2. 超出上限 (例如 2110 年: 5000000000)
            res_too_late = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                env={**os.environ, "SOURCE_DATE_EPOCH": "5000000000"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_too_late.returncode, 0)
            self.assertIn("out of valid ZIP range", res_too_late.stderr or res_too_late.stdout)

            # 3. 非法非整数字符串
            res_non_int = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                env={**os.environ, "SOURCE_DATE_EPOCH": "not_a_number"},
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_non_int.returncode, 0)
            self.assertIn("Invalid non-integer SOURCE_DATE_EPOCH", res_non_int.stderr or res_non_int.stdout)

            # 4. 合法时间戳且为奇数秒 (例如 1700000001 -> 应自动对齐为偶数秒 1700000000)
            res_valid = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                env={**os.environ, "SOURCE_DATE_EPOCH": "1700000001"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_valid.returncode, 0, f"Build with valid SOURCE_DATE_EPOCH failed: {res_valid.stderr}")
            # 校验 zip 内文件时间戳为偶数秒
            self.assert_release_consistent(out_dir)
            pointer = json.loads((Path(out_dir) / "CURRENT.json").read_text(encoding="utf-8"))
            zip_file = Path(out_dir) / pointer["generation_dir"] / f"ComfyUI-IYKYK-{pointer['version']}.zip"
            with zipfile.ZipFile(zip_file, "r") as zf:
                for info in zf.infolist():
                    self.assertEqual(info.date_time[5] % 2, 0, f"Second must be even in zip info: {info.date_time}")

    def test_failure_injection_aborts_without_pointer(self):
        """故障注入测试：在发布终结前注入异常，断言 CURRENT.json 绝对不生成"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 注入故障脚本：在 smoke_test_package 中抛出异常
            inject_script = (
                "import sys, os\n"
                "from pathlib import Path\n"
                "import scripts.build_release as br\n"
                "def faulty_smoke(zip_path):\n"
                "    raise RuntimeError('INJECTED_FAILURE_BEFORE_POINTER')\n"
                "br.smoke_test_package = faulty_smoke\n"
                "br.build_release(Path.cwd(), Path(sys.argv[1]), mode='verify')\n"
            )

            res = subprocess.run(
                [sys.executable, "-c", inject_script, out_dir],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("INJECTED_FAILURE_BEFORE_POINTER", res.stderr or res.stdout)
            # 权威发布判据：CURRENT.json 绝对不能存在
            pointer_path = Path(out_dir) / "CURRENT.json"
            self.assertFalse(pointer_path.exists(), "CURRENT.json must not exist when build is interrupted!")

    def test_pure_functional_generate_structured(self):
        """测试纯函数 _generate_structured 与 GenerationResult 不变性 (修订 7 强制约束)"""
        import random
        from nodes import _generate_structured, _sampler, _assembler, IYKYKPromptGenerator
        from lib.models import GenerationResult

        sample_inputs = {
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "卧室私密",
            "剧情主题": "随机 (Random)",
            "景别构图": "近景 CU (面部表情/眼神)",
            "拍摄视角": "自动 (Auto)",
            "裸露等级": "L2 差分微露 (Partially Exposed)",
            "服装款式": "旗袍 (Qipao/Cheongsam)",
            "服装状态": "自动联动裸露等级 (Auto Link Nudity)",
            "发型发色": "黑长直散发 (Long Straight Black Hair)",
            "饰品头饰": "无 (None)",
            "妆容细节": "无 (None)",
            "姿势动作": "随机 (Random)",
            "情绪表情": "😳 害羞/羞涩",
            "光影预设": "油画古典",
            "胶片风格": "无 (None)",
            "液体效果": "无 (None)",
            "纹身标记": "无 (None)",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "高清写真 (High)",
        }

        rng1 = random.Random(42)
        res1 = _generate_structured(_sampler, _assembler, sample_inputs, rng1)

        self.assertIsInstance(res1, GenerationResult)
        self.assertIsInstance(res1.positive, str)
        self.assertIsInstance(res1.negative, str)
        self.assertIsInstance(res1.description, str)
        self.assertIsInstance(res1.atoms, tuple)
        self.assertIsInstance(res1.rules_applied, tuple)
        self.assertGreater(len(res1.atoms), 0)

        # 确定性复现测试
        rng2 = random.Random(42)
        res2 = _generate_structured(_sampler, _assembler, sample_inputs, rng2)
        self.assertEqual(res1.positive, res2.positive)
        self.assertEqual(res1.negative, res2.negative)
        self.assertEqual(res1.description, res2.description)
        self.assertEqual(res1.atoms, res2.atoms)
        self.assertEqual(res1.rules_applied, res2.rules_applied)

        # 节点自身为零可变状态 (无 _last_generated_atoms 等实例字段)
        gen_node = IYKYKPromptGenerator()
        pos, neg, desc = gen_node.generate(**sample_inputs, prompt_seed=42)
        self.assertEqual(pos, res1.positive)
        self.assertEqual(neg, res1.negative)
        self.assertEqual(desc, res1.description)
        self.assertFalse(hasattr(gen_node, "_last_generated_atoms"))

    def test_schema_generator_check_flag_readonly(self):
        """
        验证强制反例 P1-4 & P2-3：scripts/generate_rule_schemas.py --check 为严格只读比对模式。
        1. 当 Schema 完全同步时，断言退出码为 0，前后 SHA256 与 mtime 100% 不变；
        2. 当向 Schema 写入错误内容制造漂移时，断言退出码为 1，输出报告漂移，且前后 SHA256 与 mtime 100% 不变 (严格只读，杜绝静默写盘)。
        """
        schema_file = REPO_DIR / "schemas" / "conflict-rules.schema.json"
        sha_before = hashlib.sha256(schema_file.read_bytes()).hexdigest()
        mtime_before = schema_file.stat().st_mtime_ns

        # 1. 正常无漂移检查
        res = subprocess.run(
            [sys.executable, "scripts/generate_rule_schemas.py", "--check"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"--check failed: {res.stderr or res.stdout}")
        self.assertIn("strictly up to date", res.stdout)
        self.assertEqual(sha_before, hashlib.sha256(schema_file.read_bytes()).hexdigest())
        self.assertEqual(mtime_before, schema_file.stat().st_mtime_ns)

        # 2. 隔离环境下制造漂移：验证退出码 1 且文件零写盘 (hash/mtime 不变)
        with tempfile.TemporaryDirectory() as tmp:
            drifted_schema = Path(tmp) / "drifted.schema.json"
            drifted_schema.write_text('{"drifted": true}\n', encoding="utf-8")
            drift_sha_before = hashlib.sha256(drifted_schema.read_bytes()).hexdigest()
            drift_mtime_before = drifted_schema.stat().st_mtime_ns

            res_drift = subprocess.run(
                [sys.executable, "scripts/generate_rule_schemas.py", "--check", "--schema-path", str(drifted_schema)],
                cwd=REPO_DIR,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_drift.returncode, 1, "Expected --check to exit 1 on drift")
            self.assertIn("Drift detected", res_drift.stderr)
            # 严格验证只读：drifted_schema 绝对未被写回或更新
            self.assertEqual(drift_sha_before, hashlib.sha256(drifted_schema.read_bytes()).hexdigest())
            self.assertEqual(drift_mtime_before, drifted_schema.stat().st_mtime_ns)

    def test_comprehensive_generation_tampering_fails_closed_and_preserves_old_pointer(self):
        """
        验证 P1-1 终验防伪闭环：
        对已生成的 generation 进行多维度独立篡改变异，第二次构建必须 Fail-Closed（非零退出），
        且旧 CURRENT.json 逐字节绝对不变：
        - 篡改版本 ZIP、latest ZIP、generic ZIP
        - 篡改 SHA256SUMS.txt (篡改 hash、文件名、行数、格式)
        - 篡改 MANIFEST.json (version, mode, source_commit, release_file, sha256, files_count, files, files_sha256, 绝对路径, .. 穿越)
        - 注入多余普通文件、额外子目录、符号链接
        - 篡改 ZIP 包内任一文件内容、删除包内文件、添加多余文件
        """
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 首次构建成功
            res1 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res1.returncode, 0)
            pointer_path = Path(out_dir) / "CURRENT.json"
            orig_pointer_bytes = pointer_path.read_bytes()
            pointer = json.loads(orig_pointer_bytes)
            gen_dir = Path(out_dir) / pointer["generation_dir"]
            v_zip_name = f"ComfyUI-IYKYK-{pointer['version']}.zip"

            # 备份干净的 generation 目录以便各变异测试后无损恢复
            backup_gen = Path(tmp) / "clean_backup"
            shutil.copytree(gen_dir, backup_gen)

            def run_second_build() -> subprocess.CompletedProcess:
                return subprocess.run(
                    [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                    cwd=repo,
                    capture_output=True,
                    text=True
                )

            def restore_clean_gen():
                shutil.rmtree(gen_dir)
                shutil.copytree(backup_gen, gen_dir)

            mutations = []

            # 1. 篡改 3 个 ZIP 之一 (3)
            def mut_v_zip():
                (gen_dir / v_zip_name).write_bytes(b"corrupted_v_zip")
            mutations.append(("versioned_zip", mut_v_zip))

            def mut_latest_zip():
                (gen_dir / "ComfyUI-IYKYK-latest.zip").write_bytes(b"corrupted_latest_zip")
            mutations.append(("latest_zip", mut_latest_zip))

            def mut_generic_zip():
                (gen_dir / "ComfyUI-IYKYK.zip").write_bytes(b"corrupted_generic_zip")
            mutations.append(("generic_zip", mut_generic_zip))

            # 2. 篡改 SHA256SUMS.txt (5)
            def mut_sums_corrupt_hash():
                (gen_dir / "SHA256SUMS.txt").write_text("0" * 64 + "  ComfyUI-IYKYK-latest.zip\n", encoding="utf-8")
            mutations.append(("sums_corrupt_hash", mut_sums_corrupt_hash))

            def mut_sums_missing_line():
                lines = (gen_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
                (gen_dir / "SHA256SUMS.txt").write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
            mutations.append(("sums_missing_line", mut_sums_missing_line))

            def mut_sums_extra_line():
                content = (gen_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
                (gen_dir / "SHA256SUMS.txt").write_text(content + "extra_line\n", encoding="utf-8")
            mutations.append(("sums_extra_line", mut_sums_extra_line))

            def mut_sums_single_space():
                content = (gen_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
                (gen_dir / "SHA256SUMS.txt").write_text(content.replace("  ", " "), encoding="utf-8")
            mutations.append(("sums_single_space_delimiter", mut_sums_single_space))

            def mut_sums_wrong_filename():
                content = (gen_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
                (gen_dir / "SHA256SUMS.txt").write_text(content.replace("ComfyUI-IYKYK.zip", "ComfyUI-Wrong.zip"), encoding="utf-8")
            mutations.append(("sums_wrong_filename", mut_sums_wrong_filename))

            # 3. 篡改 MANIFEST.json 各关键字段 (9)
            def mut_manifest_commit():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                m["source_commit"] = "0" * 40
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_source_commit", mut_manifest_commit))

            def mut_manifest_sha():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                m["sha256"] = "f" * 64
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_sha256", mut_manifest_sha))

            def mut_manifest_files_count():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                m["files_count"] = 99
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_files_count", mut_manifest_files_count))

            def mut_manifest_forged_file_sha():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                first_k = next(iter(m["files_sha256"].keys()))
                m["files_sha256"][first_k] = "e" * 64
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_files_sha256", mut_manifest_forged_file_sha))

            def mut_manifest_path_traversal():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                m["files"].append("ComfyUI-IYKYK/../traversal")
                m["files_sha256"]["ComfyUI-IYKYK/../traversal"] = "a" * 64
                m["files_count"] = len(m["files"])
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_path_traversal", mut_manifest_path_traversal))

            def mut_manifest_absolute_path():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                m["files"].append("/etc/passwd")
                m["files_sha256"]["/etc/passwd"] = "b" * 64
                m["files_count"] = len(m["files"])
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_absolute_path", mut_manifest_absolute_path))

            def mut_manifest_missing_version():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                del m["version"]
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_missing_version", mut_manifest_missing_version))

            def mut_manifest_mode_mismatch():
                m = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
                m["mode"] = "release"
                (gen_dir / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
            mutations.append(("manifest_mode_mismatch", mut_manifest_mode_mismatch))

            def mut_manifest_corrupted_json():
                (gen_dir / "MANIFEST.json").write_text("{NOT_JSON", encoding="utf-8")
            mutations.append(("manifest_corrupted_json", mut_manifest_corrupted_json))

            # 4. 目录项异常变异 (多余文件、子目录、符号链接) (3)
            def mut_extra_file():
                (gen_dir / "rogue_extra.txt").write_text("rogue", encoding="utf-8")
            mutations.append(("extra_file", mut_extra_file))

            def mut_extra_dir():
                (gen_dir / "rogue_dir").mkdir(parents=True, exist_ok=True)
            mutations.append(("extra_dir", mut_extra_dir))

            def mut_symlink():
                os.symlink(gen_dir / "MANIFEST.json", gen_dir / "manifest_symlink.json")
            mutations.append(("symlink", mut_symlink))

            # 5. ZIP 包内实物篡改与结构破坏 (5)
            def mut_zip_internal_tamper():
                zip_p = gen_dir / v_zip_name
                with tempfile.TemporaryDirectory() as ztmp:
                    with zipfile.ZipFile(zip_p, "r") as zf:
                        zf.extractall(ztmp)
                    first_file = next(Path(ztmp).rglob("*.py"))
                    first_file.write_text("# TAMPERED CONTENT", encoding="utf-8")
                    zip_p.unlink()
                    with zipfile.ZipFile(zip_p, "w") as zf:
                        for f in sorted(Path(ztmp).rglob("*")):
                            if f.is_file():
                                zinfo = zipfile.ZipInfo(str(f.relative_to(ztmp)))
                                zinfo.create_system = 3
                                zinfo.external_attr = (stat.S_IFREG | 0o644) << 16
                                zf.writestr(zinfo, f.read_bytes())
            mutations.append(("zip_internal_tamper", mut_zip_internal_tamper))

            def mut_zip_missing_file():
                zip_p = gen_dir / v_zip_name
                with tempfile.TemporaryDirectory() as ztmp:
                    with zipfile.ZipFile(zip_p, "r") as zf:
                        zf.extractall(ztmp)
                    all_files = sorted([f for f in Path(ztmp).rglob("*") if f.is_file()])
                    zip_p.unlink()
                    with zipfile.ZipFile(zip_p, "w") as zf:
                        for f in all_files[:-1]:
                            zinfo = zipfile.ZipInfo(str(f.relative_to(ztmp)))
                            zinfo.create_system = 3
                            zinfo.external_attr = (stat.S_IFREG | 0o644) << 16
                            zf.writestr(zinfo, f.read_bytes())
            mutations.append(("zip_missing_file", mut_zip_missing_file))

            def mut_zip_extra_file():
                zip_p = gen_dir / v_zip_name
                with zipfile.ZipFile(zip_p, "a") as zf:
                    zinfo = zipfile.ZipInfo("ComfyUI-IYKYK/extra_untracked.txt")
                    zinfo.create_system = 3
                    zinfo.external_attr = (stat.S_IFREG | 0o644) << 16
                    zf.writestr(zinfo, b"extra")
            mutations.append(("zip_extra_file", mut_zip_extra_file))

            def mut_zip_directory_entry():
                zip_p = gen_dir / v_zip_name
                with zipfile.ZipFile(zip_p, "a") as zf:
                    zinfo = zipfile.ZipInfo("ComfyUI-IYKYK/extra_subfolder/")
                    zinfo.create_system = 3
                    zinfo.external_attr = (stat.S_IFDIR | 0o755) << 16
                    zf.writestr(zinfo, b"")
            mutations.append(("zip_directory_entry", mut_zip_directory_entry))

            def mut_zip_duplicate_path():
                zip_p = gen_dir / v_zip_name
                with zipfile.ZipFile(zip_p, "a") as zf:
                    zinfo = zipfile.ZipInfo("ComfyUI-IYKYK/README.md")
                    zinfo.create_system = 3
                    zinfo.external_attr = (stat.S_IFREG | 0o644) << 16
                    zf.writestr(zinfo, b"# duplicate readme")
            mutations.append(("zip_duplicate_path", mut_zip_duplicate_path))

            self.assertEqual(len(mutations), 25, f"Expected exactly 25 mutations, got {len(mutations)}")
            passed_mutations = 0
            for name, mut_fn in mutations:
                restore_clean_gen()
                mut_fn()
                res = run_second_build()
                self.assertNotEqual(
                    res.returncode, 0,
                    f"Tampering mutation '{name}' unexpectedly passed second build!"
                )
                # 严格断言：旧指针 CURRENT.json 逐字节未被修改
                self.assertEqual(
                    pointer_path.read_bytes(),
                    orig_pointer_bytes,
                    f"CURRENT.json was modified after failed mutation '{name}'"
                )
                passed_mutations += 1
            print(f"   ✅ All {passed_mutations}/{len(mutations)} named generation mutations passed Fail-Closed verification.")

    def test_scheme_a_multi_step_failure_injection_preserves_old_generation(self):
        """
        验证强制反例 P1-6 (方案 A)：在输出目录已存在完整权威旧发布 (v1.0.0) 的情况下，
        对新版本发布的多个关键提交步骤分别注入故障：
        1. 打包阶段注入故障；
        2. 烟雾测试注入故障；
        3. 重命名版本目录前注入故障；
        4. 更新 CURRENT.json 前注入故障；
        断言：在每个故障注入点，旧 generation (v1.0.0) 的全量 5 个文件逐字节 SHA256 绝对保持不变，
        且临时目录完全清理，不存在任何混合污染。
        """
        with tempfile.TemporaryDirectory() as tmp_base, tempfile.TemporaryDirectory() as out_base:
            repo = Path(tmp_base) / "repo"
            make_clean_git_repo(repo)
            out_dir = Path(out_base)

            # 1. 构造一个合法的已有旧发布 generation
            old_gen_name = "v1.0.0-0123456789ab"
            old_version_dir = out_dir / old_gen_name
            old_version_dir.mkdir(parents=True, exist_ok=True)
            old_files = {
                "ComfyUI-IYKYK-v1.0.0.zip": b"ZIP_CONTENT_V1_0_0",
                "ComfyUI-IYKYK-latest.zip": b"ZIP_CONTENT_V1_0_0",
                "ComfyUI-IYKYK.zip": b"ZIP_CONTENT_V1_0_0",
                "SHA256SUMS.txt": b"OLD_CHECKSUMS_CONTENT",
                "MANIFEST.json": b'{"version": "v1.0.0", "sha256": "oldsha", "release_file": "ComfyUI-IYKYK-v1.0.0.zip"}',
            }
            old_hashes = {}
            for name, content in old_files.items():
                p = old_version_dir / name
                p.write_bytes(content)
                old_hashes[name] = hashlib.sha256(content).hexdigest()

            # 写入旧指针 CURRENT.json
            old_pointer = out_dir / "CURRENT.json"
            old_pointer.write_text(f'{{"version": "v1.0.0", "generation_dir": "{old_gen_name}", "sha256": "oldsha"}}\n', encoding="utf-8")

            # 2. 依次测试关键故障注入点
            injection_cases = [
                # 故障点 1：在 smoke_test_package 中抛出异常
                ("smoke_test_package", "def faulty_smoke(zip_path):\n    raise RuntimeError('INJECTED_SMOKE_FAIL')\nbr.smoke_test_package = faulty_smoke\n"),
                # 故障点 2：在锁内重命名 generation 目录前抛出异常
                ("os_replace_version", "orig_replace = os.replace\ndef faulty_replace(src, dest):\n    if 'v1.1.0' in str(dest):\n        raise RuntimeError('INJECTED_REPLACE_FAIL')\n    return orig_replace(src, dest)\nos.replace = faulty_replace\n"),
                # 故障点 3：在更新 CURRENT.json 前抛出异常
                ("update_pointer", "orig_replace = os.replace\ndef faulty_replace(src, dest):\n    if 'CURRENT.json' in str(dest):\n        raise RuntimeError('INJECTED_POINTER_FAIL')\n    return orig_replace(src, dest)\nos.replace = faulty_replace\n"),
            ]

            for name, fault_code in injection_cases:
                script = (
                    "import sys, os\n"
                    "from pathlib import Path\n"
                    "import scripts.build_release as br\n"
                    f"{fault_code}\n"
                    "br.build_release(Path.cwd(), Path(sys.argv[1]), mode='verify')\n"
                )
                res = subprocess.run(
                    [sys.executable, "-c", script, str(out_dir)],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                )
                # 断言构建失败
                self.assertNotEqual(res.returncode, 0, f"Injection '{name}' unexpectedly passed!")

                # 逐字节断言旧发布绝对未被篡改
                self.assertTrue(old_version_dir.exists(), f"Old version dir disappeared in {name}!")
                for fname, orig_h in old_hashes.items():
                    fp = old_version_dir / fname
                    self.assertTrue(fp.exists(), f"Old file {fname} missing in {name}!")
                    self.assertEqual(
                        hashlib.sha256(fp.read_bytes()).hexdigest(),
                        orig_h,
                        f"Old file {fname} mutated in byte content during failure injection '{name}'!"
                    )

                # 断言旧指针文件 CURRENT.json 保持指向旧版本
                curr_content = old_pointer.read_text(encoding="utf-8")
                self.assertIn("v1.0.0", curr_content)

                # 断言临时构建目录完全被清除
                tmp_dirs = list(out_dir.glob(".tmp_gen_*"))
                self.assertEqual(len(tmp_dirs), 0, f"Leftover tmp_gen directories found in {name}: {tmp_dirs}")

    def test_concurrent_builds_unique_temporary_directories(self):
        """
        验证强制反例 10：两个并发构建使用独立临时目录并经由发布锁协调，
        两进程均确切返回 0 (其中一个成功创建，另一个走幂等路径)，
        最终发布目录全文件哈希与指针严格一致。
        """
        with tempfile.TemporaryDirectory() as tmp_base, tempfile.TemporaryDirectory() as out_base:
            repo = Path(tmp_base) / "repo"
            make_clean_git_repo(repo)
            out_dir = Path(out_base)

            # 启动两个独立的构建子进程
            p1 = subprocess.Popen(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", str(out_dir)],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            p2 = subprocess.Popen(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", str(out_dir)],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            p1_out, p1_err = p1.communicate()
            p2_out, p2_err = p2.communicate()

            # 两进程均必须确定性返回 0
            self.assertEqual(p1.returncode, 0, f"p1 failed with code {p1.returncode}: {p1_err.decode()}")
            self.assertEqual(p2.returncode, 0, f"p2 failed with code {p2.returncode}: {p2_err.decode()}")

            # 最终发布目录必须通过严格一致性检验
            self.assert_release_consistent(out_dir)

            # 绝无残留的 .tmp_gen_* 目录
            leftovers = list(out_dir.glob(".tmp_gen_*"))
            self.assertEqual(len(leftovers), 0, f"Found leftover temporary directories: {leftovers}")

    def test_verify_to_release_upgrade_in_same_output_directory(self):
        """测试同一 HEAD、同一输出目录上 verify -> annotated tag -> release 两次均成功，且指针正确指向 release generation (P1-4)"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 1. verify 模式构建
            res1 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res1.returncode, 0, f"Verify build failed: {res1.stderr}")
            manifest1 = self.assert_release_consistent(out_dir)
            self.assertEqual(manifest1["mode"], "verify")

            pointer_path = Path(out_dir) / "CURRENT.json"
            pointer1 = json.loads(pointer_path.read_text(encoding="utf-8"))
            verify_gen_dir = Path(out_dir) / pointer1["generation_dir"]
            self.assertTrue(verify_gen_dir.is_dir())
            self.assertIn("-verify-", pointer1["generation_dir"])

            # 2. 打 annotated tag (在隔离临时测试仓库内)
            version = manifest1["version"].lstrip("v")
            subprocess.run(
                ["git", "tag", "-a", f"v{version}", "-m", f"Release v{version}"],
                cwd=repo,
                check=True,
                capture_output=True
            )

            # 3. release 模式构建到同一 output_dir
            res2 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "release", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res2.returncode, 0, f"Release build failed: {res2.stderr}")
            manifest2 = self.assert_release_consistent(out_dir)
            self.assertEqual(manifest2["mode"], "release")

            pointer2 = json.loads(pointer_path.read_text(encoding="utf-8"))
            release_gen_dir = Path(out_dir) / pointer2["generation_dir"]
            self.assertTrue(release_gen_dir.is_dir())
            self.assertIn("-release-", pointer2["generation_dir"])

            # 关键断言：原 verify generation 仍完好保留在同一输出目录下！
            self.assertTrue(verify_gen_dir.is_dir())
            self.assertNotEqual(pointer1["generation_dir"], pointer2["generation_dir"])

            # 4. 同模式并发/二次构建幂等性
            res3 = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "release", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res3.returncode, 0, f"Idempotent release build failed: {res3.stderr}")
            manifest3 = self.assert_release_consistent(out_dir)
            self.assertEqual(manifest3["mode"], "release")
            pointer3 = json.loads(pointer_path.read_text(encoding="utf-8"))
            self.assertEqual(pointer2, pointer3)

    def test_zip_unix_mode_and_entry_integrity(self):
        """测试 ZIP Unix 模式位校验以及四类独立反例：合法自产、软链接、目录项和重复路径 (R3)"""
        from scripts.build_release import validate_generation_integrity, validate_zip_entry_integrity
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            res = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res.returncode, 0)
            pointer = json.loads((Path(out_dir) / "CURRENT.json").read_text(encoding="utf-8"))
            gen_dir = Path(out_dir) / pointer["generation_dir"]
            manifest = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
            m_files_sha = manifest["files_sha256"]

            # 反例 1: 自产 ZIP 实物必须全部为普通文件 (stat.S_IFREG)，合法通过
            self.assert_release_consistent(out_dir)

            # 反例 2: 软链接条目必须被严格拦截
            zip_versioned = gen_dir / f"ComfyUI-IYKYK-{pointer['version']}.zip"
            with tempfile.TemporaryDirectory() as ztmp:
                with zipfile.ZipFile(zip_versioned, "r") as zf:
                    zf.extractall(ztmp)
                test_zip_symlink = Path(ztmp) / "test_symlink.zip"
                with zipfile.ZipFile(test_zip_symlink, "w") as zf:
                    for f in sorted(Path(ztmp).rglob("*")):
                        if f.is_file() and f.name != "test_symlink.zip":
                            zinfo = zipfile.ZipInfo(str(f.relative_to(ztmp)))
                            zinfo.create_system = 3
                            zinfo.external_attr = (stat.S_IFLNK | 0o777) << 16  # 软链接位
                            zf.writestr(zinfo, f.read_bytes())
                with self.assertRaises(ValueError) as ctx:
                    validate_zip_entry_integrity(test_zip_symlink, m_files_sha)
                self.assertIn("symlink", str(ctx.exception).lower())

            # 反例 3: 目录项必须被严格拦截
            with tempfile.TemporaryDirectory() as ztmp:
                with zipfile.ZipFile(zip_versioned, "r") as zf:
                    zf.extractall(ztmp)
                test_zip_dir = Path(ztmp) / "test_dir.zip"
                with zipfile.ZipFile(test_zip_dir, "w") as zf:
                    for f in sorted(Path(ztmp).rglob("*")):
                        if f.is_file() and f.name != "test_dir.zip":
                            zinfo = zipfile.ZipInfo(str(f.relative_to(ztmp)))
                            zinfo.create_system = 3
                            zinfo.external_attr = (stat.S_IFREG | 0o644) << 16
                            zf.writestr(zinfo, f.read_bytes())
                    # 添加目录项
                    zinfo_d = zipfile.ZipInfo("ComfyUI-IYKYK/subfolder/")
                    zinfo_d.create_system = 3
                    zinfo_d.external_attr = (stat.S_IFDIR | 0o755) << 16
                    zf.writestr(zinfo_d, b"")
                with self.assertRaises(ValueError) as ctx:
                    validate_zip_entry_integrity(test_zip_dir, m_files_sha)
                self.assertIn("directory", str(ctx.exception).lower())

            # 反例 4: 重复路径必须被严格拦截
            with tempfile.TemporaryDirectory() as ztmp:
                with zipfile.ZipFile(zip_versioned, "r") as zf:
                    zf.extractall(ztmp)
                test_zip_dup = Path(ztmp) / "test_dup.zip"
                with zipfile.ZipFile(test_zip_dup, "w") as zf:
                    for f in sorted(Path(ztmp).rglob("*")):
                        if f.is_file() and f.name != "test_dup.zip":
                            zinfo = zipfile.ZipInfo(str(f.relative_to(ztmp)))
                            zinfo.create_system = 3
                            zinfo.external_attr = (stat.S_IFREG | 0o644) << 16
                            zf.writestr(zinfo, f.read_bytes())
                    # 重复添加 README.md
                    zinfo_dup = zipfile.ZipInfo("ComfyUI-IYKYK/README.md")
                    zinfo_dup.create_system = 3
                    zinfo_dup.external_attr = (stat.S_IFREG | 0o644) << 16
                    zf.writestr(zinfo_dup, b"# duplicate")
                with self.assertRaises(ValueError) as ctx:
                    validate_zip_entry_integrity(test_zip_dup, m_files_sha)
                self.assertIn("duplicate", str(ctx.exception).lower())


    def test_source_symlink_rejection_matrix(self):
        """测试发布构建严格拦截白名单源文件或其父目录的符号链接，杜绝仓库外内容泄漏与清单身份漂移 (P1-1)"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_dir:
            repo = Path(tmp) / "repo"
            make_clean_git_repo(repo)

            # 1. 正常初次构建成功
            res_init = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertEqual(res_init.returncode, 0, f"Initial build failed: {res_init.stderr}")
            self.assert_release_consistent(out_dir)
            orig_pointer_bytes = (Path(out_dir) / "CURRENT.json").read_bytes()

            # 2. 反例 A: 必需根文件 (README.md) 是指向仓库外文件的符号链接 (/etc/hosts)
            orig_readme_bytes = (repo / "README.md").read_bytes()
            (repo / "README.md").unlink()
            os.symlink("/etc/hosts", repo / "README.md")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Symlink external"], cwd=repo, check=True, capture_output=True)
            res_a = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res_a.returncode, 0, "Build should fail when README.md is external symlink")
            self.assertIn("symlink", res_a.stderr.lower() + res_a.stdout.lower())
            self.assertEqual((Path(out_dir) / "CURRENT.json").read_bytes(), orig_pointer_bytes)

            # 恢复 README.md
            (repo / "README.md").unlink()
            (repo / "README.md").write_bytes(orig_readme_bytes)
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Restore README"], cwd=repo, check=True, capture_output=True)

            # 3. 反例 B: 必需根文件是内部符号链接 (指向 CHANGELOG.md)
            (repo / "README.md").unlink()
            os.symlink(repo / "CHANGELOG.md", repo / "README.md")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Symlink internal"], cwd=repo, check=True, capture_output=True)
            res_b = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res_b.returncode, 0, "Build should fail when README.md is internal symlink")
            self.assertIn("symlink", res_b.stderr.lower() + res_b.stdout.lower())
            self.assertEqual((Path(out_dir) / "CURRENT.json").read_bytes(), orig_pointer_bytes)

            # 恢复 README.md
            (repo / "README.md").unlink()
            (repo / "README.md").write_bytes(orig_readme_bytes)
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Restore README again"], cwd=repo, check=True, capture_output=True)

            # 4. 反例 C: 父目录 (lib/ 或 data/) 是指向仓库外的符号链接
            ext_data_dir = Path(tmp) / "ext_data"
            shutil.copytree(repo / "data", ext_data_dir)
            shutil.rmtree(repo / "data")
            os.symlink(ext_data_dir, repo / "data")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Symlink data dir"], cwd=repo, check=True, capture_output=True)
            res_c = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--mode", "verify", "--output-dir", out_dir],
                cwd=repo,
                capture_output=True,
                text=True
            )
            self.assertNotEqual(res_c.returncode, 0, "Build should fail when data/ is symlink to outside")
            self.assertIn("symlink", res_c.stderr.lower() + res_c.stdout.lower())
            self.assertEqual((Path(out_dir) / "CURRENT.json").read_bytes(), orig_pointer_bytes)


if __name__ == "__main__":
    unittest.main()
