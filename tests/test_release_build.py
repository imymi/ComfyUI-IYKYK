"""
test_release_build.py — 自动化构建与发布工程测试门禁
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.runtime_manifest import RUNTIME_DATA_FILES


class TestReleaseBuild(unittest.TestCase):
    """测试发布脚本在独立隔离目录中的行为与确定性可重复构建"""

    def test_version_mismatch_aborts_without_mutating_source(self):
        """测试版本不一致时构建立即报错，且绝不修改 js/version.js"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_repo = Path(tmp) / "ComfyUI-IYKYK"
            shutil.copytree(REPO_DIR, tmp_repo)

            # 故意制造版本不匹配
            version_file = tmp_repo / "js" / "version.js"
            version_file.write_text('export const EXTENSION_VERSION = "v0.0.0-mismatch";\n')

            res = subprocess.run(
                [sys.executable, "scripts/build_release.py"],
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

    def test_dual_build_deterministic_sha256_match(self):
        """测试在两个独立临时目录构建出的 Release ZIP SHA256 绝对一致"""
        with tempfile.TemporaryDirectory() as out_a, tempfile.TemporaryDirectory() as out_b:
            res_a = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--output-dir", out_a],
                cwd=REPO_DIR,
                capture_output=True,
                text=True
            )
            self.assertEqual(res_a.returncode, 0, f"Build A failed: {res_a.stderr}")
            zip_a = next(Path(out_a).glob("ComfyUI-IYKYK-v*.zip"))
            hash_a = hashlib.sha256(zip_a.read_bytes()).hexdigest()

            res_b = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--output-dir", out_b],
                cwd=REPO_DIR,
                capture_output=True,
                text=True
            )
            self.assertEqual(res_b.returncode, 0, f"Build B failed: {res_b.stderr}")
            zip_b = next(Path(out_b).glob("ComfyUI-IYKYK-v*.zip"))
            hash_b = hashlib.sha256(zip_b.read_bytes()).hexdigest()

            self.assertEqual(hash_a, hash_b, "Dual builds produced different SHA256 hashes!")

            # 校验 ZIP 内文件数量与 19 个数据文件
            with zipfile.ZipFile(zip_a, "r") as zf:
                namelist = zf.namelist()
                self.assertEqual(len(namelist), 33)  # 6 root + 4 js + 4 lib + 19 data
                data_entries = [n for n in namelist if n.startswith("ComfyUI-IYKYK/data/")]
                self.assertEqual(len(data_entries), 19)
                for df in RUNTIME_DATA_FILES:
                    self.assertIn(f"ComfyUI-IYKYK/data/{df}", namelist)

    def test_smoke_test_from_arbitrary_temp_directory(self):
        """测试即使从 /private/tmp 等完全包外的目录执行烟测，也能 100% 独立运行通过"""
        with tempfile.TemporaryDirectory() as out_dir:
            res = subprocess.run(
                [sys.executable, "scripts/build_release.py", "--output-dir", out_dir],
                cwd=REPO_DIR,
                capture_output=True,
                text=True
            )
            self.assertEqual(res.returncode, 0)
            zip_path = next(Path(out_dir).glob("ComfyUI-IYKYK-v*.zip"))

            # 在另一个临时目录执行解压与测试
            with tempfile.TemporaryDirectory() as test_sandbox:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(test_sandbox)
                extracted_dir = Path(test_sandbox) / "ComfyUI-IYKYK"

                script = f"""
import sys
import os
sys.path.insert(0, r'{extracted_dir}')
import nodes
from lib.runtime_manifest import RUNTIME_DATA_FILES

assert len(RUNTIME_DATA_FILES) == 19
gen = nodes.IYKYKPromptGenerator()
pos, neg, desc = gen.generate(
    预设模板='无 (None)',
    风格配方='无 (None)',
    场景大类='随机 (Random)',
    剧情主题='随机 (Random)',
    景别构图='自动 (Auto)',
    拍摄视角='自动 (Auto)',
    裸露等级='L2 差分微露 (Partially Exposed)',
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
assert len(pos) > 0
print('PASS')
"""
                smoke_res = subprocess.run(
                    [sys.executable, "-I", "-c", script],
                    cwd=extracted_dir,
                    env={"PATH": os.environ.get("PATH", "")},
                    capture_output=True,
                    text=True
                )
                self.assertEqual(smoke_res.returncode, 0, f"Isolated smoke failed: {smoke_res.stderr}")
                self.assertIn("PASS", smoke_res.stdout)


if __name__ == "__main__":
    unittest.main()
