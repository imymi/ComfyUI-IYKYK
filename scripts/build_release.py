#!/usr/bin/env python3
"""
build_release.py — ComfyUI-IYKYK 确定性可重复构建与纯净打包脚本

特性：
1. 动态从 pyproject.toml 读取单一事实版本号并自动同步更新 js/version.js
2. 打包前执行 validate_data.py 强门禁校验，失败立即中断
3. 支持 --output-dir 参数，方便 CI 在独立临时目录中进行双构建一致性比对
4. 严格白名单过滤：排除 source_md/, tests/, schemas/, scripts/, .git/, .github/, __pycache__/ 等
5. 规范化 ZIP 文件时间戳与权限，实现 100% 确定性可复现构建 (Deterministic / Reproducible Build)
6. 自动输出 MANIFEST.json 与 SHA256SUMS.txt
7. 构建后自动执行解压隔离环境下的全功能烟雾测试 (覆盖 3 节点、INPUT_TYPES、IS_CHANGED 与生成)
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


def sync_frontend_version(version: str):
    js_dir = REPO_DIR / "js"
    js_dir.mkdir(exist_ok=True)
    version_file = js_dir / "version.js"
    version_file.write_text(f'export const EXTENSION_VERSION = "v{version}";\n', encoding="utf-8")


def run_data_validation():
    print("🔍 Step 1: Running dataset schema and integrity validation...")
    val_script = REPO_DIR / "scripts" / "validate_data.py"
    res = subprocess.run([sys.executable, str(val_script)], capture_output=True, text=True)
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

    # 2. lib/
    lib_dir = REPO_DIR / "lib"
    if not lib_dir.is_dir():
        raise FileNotFoundError("Missing lib directory")
    for p in sorted(lib_dir.glob("*.py")):
        files.append((p, f"ComfyUI-IYKYK/lib/{p.name}"))

    # 3. data/
    data_dir = REPO_DIR / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError("Missing data directory")
    for p in sorted(data_dir.glob("*.json")):
        files.append((p, f"ComfyUI-IYKYK/data/{p.name}"))

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
sys.path.insert(0, r'{test_dir}')
import nodes

# 1. 验证 3 个节点的 INPUT_TYPES
for name, cls in nodes.NODE_CLASS_MAPPINGS.items():
    types = cls.INPUT_TYPES()
    assert isinstance(types, dict), f"INPUT_TYPES failed for {{name}}"

# 2. 验证 3 个节点的 IS_CHANGED
h1 = nodes.IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42)
h2 = nodes.IYKYKPresetBrowser.IS_CHANGED(prompt_seed=42)
h3 = nodes.IYKYKCustomSlotCombiner.IS_CHANGED(prompt_seed=42)
assert isinstance(h1, str) and len(h1) == 64
assert isinstance(h2, str) and len(h2) == 64
assert isinstance(h3, str) and len(h3) == 64

# 3. 验证生成
gen = nodes.IYKYKPromptGenerator()
pos, neg, desc = gen.generate(
    预设模板='无 (None)',
    风格配方='无 (None)',
    场景大类='随机 (Random)',
    剧情主题='随机 (Random)',
    景别构图='自动 (Auto)',
    拍摄视角='自动 (Auto)',
    裸露等级='随机 (Random)',
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
assert len(pos) > 0, 'Positive prompt empty'
assert len(neg) > 0, 'Negative prompt empty'

# 4. 验证预设
browser = nodes.IYKYKPresetBrowser()
first_preset = nodes._sampler.list_preset_names()[0]
b_pos, b_neg, b_desc = browser.browse(first_preset, '无 (None)', '高清写真 (High)', prompt_seed=42)
assert len(b_pos) > 0

# 5. 验证拼装
comb = nodes.IYKYKCustomSlotCombiner()
c_pos, c_neg, c_desc = comb.combine(prompt_seed=42, 场景主题='onsen')
assert len(c_pos) > 0

print('   ✅ Package generation smoke test passed for all 3 nodes.')
"""
        res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
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
    sync_frontend_version(version)
    print(f"🚀 Building ComfyUI-IYKYK Release v{version}...")

    # 1. 校验数据
    run_data_validation()

    # 2. 收集文件
    files_to_pack = collect_files_to_pack()
    print(f"📦 Step 2: Collected {len(files_to_pack)} runtime files for packaging.")

    zip_version_path = out_dir / f"ComfyUI-IYKYK-v{version}.zip"
    zip_latest_path = out_dir / "ComfyUI-IYKYK.zip"

    # 3. 确定性打包
    write_deterministic_zip(files_to_pack, zip_version_path)
    shutil.copy2(zip_version_path, zip_latest_path)

    # 4. 生成校验和与清单
    sha256 = hashlib.sha256(zip_version_path.read_bytes()).hexdigest()
    size_kb = zip_version_path.stat().st_size / 1024

    manifest = {
        "name": "ComfyUI-IYKYK",
        "version": version,
        "file_count": len(files_to_pack),
        "size_kb": round(size_kb, 2),
        "sha256": sha256,
        "files": [arc for _, arc in files_to_pack]
    }

    manifest_path = out_dir / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    sha256_path = out_dir / "SHA256SUMS.txt"
    sha256_path.write_text(
        f"{sha256}  ComfyUI-IYKYK-v{version}.zip\n{sha256}  ComfyUI-IYKYK.zip\n",
        encoding="utf-8"
    )

    # 5. 烟雾测试
    smoke_test_package(zip_version_path)

    print("\n🎉 Build Completed Successfully!")
    print(f"   Output dir:  {out_dir}")
    print(f"   Release ZIP: {zip_version_path.name}")
    print(f"   Files count: {len(files_to_pack)}")
    print(f"   Size:        {size_kb:.2f} KB")
    print(f"   SHA256:      {sha256}")
    print(f"   Manifest:    {manifest_path.name}")
    print(f"   Checksums:   {sha256_path.name}")


if __name__ == "__main__":
    main()
