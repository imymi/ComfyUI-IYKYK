#!/usr/bin/env python3
"""
build_release.py — ComfyUI-IYKYK 确定性可重复构建与纯净打包脚本

特性：
1. 动态从 pyproject.toml 读取单一事实版本号并对 js/version.js 执行只读校验（禁止修改源码）
2. 打包前执行 validate_data.py --strict 强门禁校验，失败立即中断
3. 支持 --output-dir 参数，方便 CI 在独立临时目录中进行双构建一致性比对
4. 严格白名单过滤：排除 source_md/, tests/, schemas/, scripts/, .git/, .github/, __pycache__/ 等
5. 规范化 ZIP 文件时间戳与权限，实现 100% 确定性可复现构建 (Deterministic / Reproducible Build)
6. 自动输出 MANIFEST.json 与 SHA256SUMS.txt
7. 构建后自动执行完全隔离环境下的全功能烟雾测试 (无源码泄漏、覆盖 3 节点、INPUT_TYPES、IS_CHANGED 与生成)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.runtime_manifest import RUNTIME_DATA_FILES

# 固定时间戳：2026-01-01 00:00:00 (保证多次构建 SHA256 绝对一致)
FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

INCLUDED_ROOT_FILES = [
    "__init__.py",
    "nodes.py",
    "pyproject.toml",
    ".comfyignore",
    "README.md",
    "CHANGELOG.md",
]


def get_project_version() -> str:
    pyproject = REPO_DIR / "pyproject.toml"
    if not pyproject.is_file():
        raise FileNotFoundError(f"Missing pyproject.toml in {REPO_DIR}")
    content = pyproject.read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', content)
    if not m:
        raise ValueError("Could not find project.version in pyproject.toml")
    return m.group(1)


def verify_frontend_version(version: str):
    version_file = REPO_DIR / "js" / "version.js"
    if not version_file.is_file():
        raise FileNotFoundError(f"Missing {version_file}! Please run scripts/sync_version.py to generate it.")
    expected_content = f'export const EXTENSION_VERSION = "v{version}";\n'
    actual_content = version_file.read_text(encoding="utf-8")
    if actual_content != expected_content:
        raise ValueError(
            f"Version mismatch! pyproject.toml has '{version}', but js/version.js has '{actual_content.strip()}'. "
            "Build does not modify source files. Run `python3 scripts/sync_version.py` to sync version manually."
        )


def run_data_validation():
    print("🔍 Step 1: Running dataset schema and integrity validation...")
    val_script = REPO_DIR / "scripts" / "validate_data.py"
    res = subprocess.run([sys.executable, str(val_script), "--strict"], capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr)
        raise RuntimeError("Data validation failed! Release packaging aborted.")
    print("   ✅ Dataset validation passed with 0 errors.")


def collect_files_to_pack() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []

    # 1. 根目录文件
    for rf in INCLUDED_ROOT_FILES:
        fp = REPO_DIR / rf
        if not fp.is_file():
            raise FileNotFoundError(f"Missing required release file: {fp}")
        files.append((fp, f"ComfyUI-IYKYK/{rf}"))

    # 2. lib/ (包含 runtime_manifest.py 等全部核心库)
    lib_dir = REPO_DIR / "lib"
    if not lib_dir.is_dir():
        raise FileNotFoundError("Missing lib directory")
    for p in sorted(lib_dir.glob("*.py")):
        files.append((p, f"ComfyUI-IYKYK/lib/{p.name}"))

    # 3. data/ (严格 19 个运行时数据文件)
    data_dir = REPO_DIR / "data"
    for df in RUNTIME_DATA_FILES:
        fp = data_dir / df
        if not fp.is_file():
            raise FileNotFoundError(f"Missing required runtime data file: {fp}")
        files.append((fp, f"ComfyUI-IYKYK/data/{df}"))

    # 4. js/
    js_dir = REPO_DIR / "js"
    if not js_dir.is_dir():
        raise FileNotFoundError("Missing js directory")
    for p in sorted(js_dir.glob("*.js")):
        files.append((p, f"ComfyUI-IYKYK/js/{p.name}"))

    # 按包内相对路径严格排序
    files.sort(key=lambda x: x[1])
    return files


def write_deterministic_zip(files: list[tuple[Path, str]], output_path: Path):
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full_path, arc_name in files:
            data = full_path.read_bytes()
            zinfo = zipfile.ZipInfo(filename=arc_name, date_time=FIXED_ZIP_TIMESTAMP)
            zinfo.external_attr = 0o644 << 16  # standard file permissions
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zinfo, data)


def smoke_test_package(zip_path: Path):
    print("🧪 Step 3: Running isolated smoke test on generated package...")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        test_dir = Path(tmp) / "ComfyUI-IYKYK"
        script = f"""
import sys
import os
import json
from pathlib import Path

# 确保包内目录优先加载
sys.path.insert(0, r'{test_dir}')
import nodes
from lib.runtime_manifest import RUNTIME_DATA_FILES

# 1. 验证没有从任何包外源码目录加载模块 (Zero Source Leak)
for mod_name, mod in list(sys.modules.items()):
    if mod and hasattr(mod, '__file__') and mod.__file__:
        if mod_name.startswith('nodes') or mod_name.startswith('lib'):
            assert mod.__file__.startswith(r'{test_dir}'), f"Leaked external module {{mod_name}} from {{mod.__file__}}"

# 2. 验证 19 个运行时数据文件全部从包内成功解析
data_dir = Path(r'{test_dir}') / 'data'
for df in RUNTIME_DATA_FILES:
    p = data_dir / df
    assert p.is_file(), f"Missing packaged data file {{df}}"
    d = json.loads(p.read_text(encoding='utf-8'))
    assert isinstance(d, dict), f"Invalid json in {{df}}"

# 3. 验证 3 个节点的 INPUT_TYPES
for name, cls in nodes.NODE_CLASS_MAPPINGS.items():
    types = cls.INPUT_TYPES()
    assert isinstance(types, dict), f"INPUT_TYPES failed for {{name}}"

# 4. 验证 3 个节点的 IS_CHANGED
h1 = nodes.IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42)
h2 = nodes.IYKYKPresetBrowser.IS_CHANGED(prompt_seed=42)
h3 = nodes.IYKYKCustomSlotCombiner.IS_CHANGED(prompt_seed=42)
assert isinstance(h1, str) and len(h1) == 64
assert isinstance(h2, str) and len(h2) == 64
assert isinstance(h3, str) and len(h3) == 64

# 5. 验证默认 Auto Link Nudity 下 L2/L3/L4 生成
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

# 6. 验证预设
browser = nodes.IYKYKPresetBrowser()
first_preset = nodes._sampler.list_preset_names()[0]
b_pos, b_neg, b_desc = browser.browse(first_preset, '无 (None)', '高清写真 (High)', prompt_seed=42)
assert len(b_pos) > 0

# 7. 验证拼装
comb = nodes.IYKYKCustomSlotCombiner()
c_pos, c_neg, c_desc = comb.combine(prompt_seed=42, 场景主题='onsen')
assert len(c_pos) > 0

print('   ✅ Package generation smoke test passed for all 3 nodes.')
"""
        isolated_env = {"PATH": os.environ.get("PATH", "")}
        res = subprocess.run([sys.executable, "-I", "-c", script], cwd=test_dir, env=isolated_env, capture_output=True, text=True)
        if res.returncode != 0:
            print(res.stdout)
            print(res.stderr)
            raise RuntimeError("Smoke test on packaged release failed!")
        print(res.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description="Deterministic release packaging for ComfyUI-IYKYK.")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory to output release zip and manifest")
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else REPO_DIR.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    version = get_project_version()
    verify_frontend_version(version)
    print(f"🚀 Building ComfyUI-IYKYK Release v{version}...")

    # 1. 校验数据
    run_data_validation()

    # 2. 收集文件
    files = collect_files_to_pack()
    print(f"📦 Step 2: Collected {len(files)} runtime files for packaging.")

    # 3. 输出 ZIP 文件
    zip_name = f"ComfyUI-IYKYK-v{version}.zip"
    zip_path = out_dir / zip_name
    write_deterministic_zip(files, zip_path)

    # 4. 生成 latest 副本
    latest_zip = out_dir / "ComfyUI-IYKYK-latest.zip"
    shutil.copyfile(zip_path, latest_zip)

    # 5. 计算校验和
    sha256_val = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    # 6. 生成 MANIFEST.json
    manifest = {
        "extension_name": "ComfyUI-IYKYK",
        "version": f"v{version}",
        "release_file": zip_name,
        "sha256": sha256_val,
        "files_count": len(files),
        "files": [arc_name for _, arc_name in files]
    }
    manifest_path = out_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 7. 生成 SHA256SUMS.txt
    sums_path = out_dir / "SHA256SUMS.txt"
    sums_content = f"{sha256_val}  {zip_name}\n{sha256_val}  ComfyUI-IYKYK-latest.zip\n"
    sums_path.write_text(sums_content, encoding="utf-8")

    # 8. 执行隔离环境烟雾测试
    smoke_test_package(zip_path)

    print(f"\n🎉 Build Completed Successfully!")
    print(f"   Output dir:  {out_dir}")
    print(f"   Release ZIP: {zip_name}")
    print(f"   Files count: {len(files)}")
    print(f"   Size:        {zip_path.stat().st_size / 1024:.2f} KB")
    print(f"   SHA256:      {sha256_val}")
    print(f"   Manifest:    {manifest_path.name}")
    print(f"   Checksums:   {sums_path.name}\n")


if __name__ == "__main__":
    main()
