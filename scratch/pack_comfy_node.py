#!/usr/bin/env python3
"""
pack_comfy_node.py — ComfyUI 纯净轻量化节点打包工具

严格从 lib.runtime_manifest.RUNTIME_PACKAGE_FILES 读取单一事实来源清单，
剔除 1.6G 的 scratch/ 离线评测与测试归档数据，
打包出供 ComfyUI 实机运行的纯净节点目录与 ZIP 压缩包 (~22.8 MB / 1.34 MB ZIP)。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.runtime_manifest import (
    RUNTIME_PACKAGE_FILES,
    TOTAL_RUNTIME_FILES_COUNT,
    RUNTIME_ROOT_FILES,
    RUNTIME_LIB_FILES,
    RUNTIME_JS_FILES,
    RUNTIME_DATA_FILES,
    RUNTIME_SCHEMA_FILES,
)


def pack():
    print("📦 Starting lightweight packaging for ComfyUI...")
    print(f"   Source directory: {REPO_DIR}")
    print(f"   Runtime manifest specifies {TOTAL_RUNTIME_FILES_COUNT} files.")

    # 1. 验证所有源文件存在
    missing = [f for f in RUNTIME_PACKAGE_FILES if not (REPO_DIR / f).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing required runtime files: {missing}")

    dist_dir = REPO_DIR / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    target_node_dir = dist_dir / "ComfyUI-IYKYK"
    if target_node_dir.exists():
        shutil.rmtree(target_node_dir)
    target_node_dir.mkdir(parents=True, exist_ok=True)

    # 2. 拷贝 43 个纯净运行时文件
    copied_count = 0
    total_raw_bytes = 0
    files_sha256 = {}

    for rel_path in sorted(RUNTIME_PACKAGE_FILES):
        src_file = REPO_DIR / rel_path
        dst_file = target_node_dir / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

        file_bytes = dst_file.read_bytes()
        copied_count += 1
        total_raw_bytes += len(file_bytes)
        files_sha256[rel_path] = hashlib.sha256(file_bytes).hexdigest()

    print(f"   ✅ Copied {copied_count} runtime files.")
    print(f"   Directory size: {total_raw_bytes / (1024 * 1024):.2f} MB")

    # 3. 生成 ZIP 压缩包 (ComfyUI-IYKYK.zip)
    zip_path = dist_dir / "ComfyUI-IYKYK.zip"
    if zip_path.exists():
        zip_path.unlink()

    # 使用固定的归一化时间戳 (2026-10-20 00:00:00) 确保确定性
    zip_ts = (2026, 10, 20, 0, 0, 0)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in sorted(RUNTIME_PACKAGE_FILES):
            fpath = target_node_dir / rel_path
            data = fpath.read_bytes()
            zinfo = zipfile.ZipInfo(filename=f"ComfyUI-IYKYK/{rel_path}", date_time=zip_ts)
            zinfo.create_system = 3  # Unix
            zinfo.external_attr = (stat.S_IFREG | 0o644) << 16
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zinfo, data)

    zip_bytes = zip_path.read_bytes()
    zip_sha = hashlib.sha256(zip_bytes).hexdigest()
    zip_size_mb = len(zip_bytes) / (1024 * 1024)
    print(f"   ✅ Created zip package: {zip_path.name} ({zip_size_mb:.2f} MB)")
    print(f"   ZIP SHA256: {zip_sha}")

    # 同时创建同名副本便于各种习惯引用 (ComfyUI-IYKYK / iykyk-affinity-override)
    alias_node_dir = dist_dir / "iykyk-affinity-override"
    if alias_node_dir.is_symlink() or alias_node_dir.is_file():
        alias_node_dir.unlink()
    elif alias_node_dir.is_dir():
        shutil.rmtree(alias_node_dir)
    alias_node_dir.symlink_to("ComfyUI-IYKYK", target_is_directory=True)

    alias_zip = dist_dir / "iykyk-affinity-override.zip"
    shutil.copyfile(zip_path, alias_zip)

    # 4. 生成打包清单与校验文件
    manifest = {
        "package_name": "ComfyUI-IYKYK",
        "description": "ComfyUI Lightweight Custom Node Package (Excluding 1.6GB test/audit scratch)",
        "files_count": copied_count,
        "raw_size_bytes": total_raw_bytes,
        "raw_size_mb": round(total_raw_bytes / (1024 * 1024), 2),
        "zip_size_bytes": len(zip_bytes),
        "zip_size_mb": round(zip_size_mb, 2),
        "zip_sha256": zip_sha,
        "files": list(sorted(RUNTIME_PACKAGE_FILES)),
        "files_sha256": files_sha256,
        "components": {
            "root_files": list(RUNTIME_ROOT_FILES),
            "lib_files": list(RUNTIME_LIB_FILES),
            "js_files": list(RUNTIME_JS_FILES),
            "data_files": list(RUNTIME_DATA_FILES),
            "schema_files": list(RUNTIME_SCHEMA_FILES),
        }
    }
    manifest_path = dist_dir / "PACKAGE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    sums_path = dist_dir / "SHA256SUMS.txt"
    sums_path.write_text(f"{zip_sha}  ComfyUI-IYKYK.zip\n{zip_sha}  iykyk-affinity-override.zip\n", encoding="utf-8")

    # 同步一份到工作区根目录 /Users/jacobyang/Hermes/dist
    workspace_dist = REPO_DIR.parent / "dist"
    workspace_dist.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(zip_path, workspace_dist / "ComfyUI-IYKYK.zip")
    shutil.copyfile(manifest_path, workspace_dist / "PACKAGE_MANIFEST.json")
    shutil.copyfile(sums_path, workspace_dist / "SHA256SUMS.txt")

    # 5. 执行隔离环境烟雾测试验证打包结果
    print("\n🧪 Running smoke test on packaged node...")
    test_node(target_node_dir)
    print("🎉 Packaging and verification complete!")
    print("\nSummary:")
    print("   - Original folder:      ~1.7 GB (dominated by 1.6GB scratch/ audit data)")
    print(f"   - Repackaged folder:    {target_node_dir} ({total_raw_bytes / (1024 * 1024):.2f} MB)")
    print(f"   - Repackaged zip:       {zip_path} ({zip_size_mb:.2f} MB)")
    print(f"   - Workspace zip:        {workspace_dist / 'ComfyUI-IYKYK.zip'}")
    print(f"   - Files in package:     {copied_count} files")


def test_node(node_dir: Path):
    """在纯净环境中测试加载与执行打包节点。"""
    with tempfile.TemporaryDirectory() as tmp:
        test_copy = Path(tmp) / "ComfyUI-IYKYK"
        shutil.copytree(node_dir, test_copy)

        # 确保无其他包泄漏
        sys_path_backup = list(sys.path)
        try:
            sys.path = [str(test_copy)] + [p for p in sys_path_backup if "iykyk" not in p and "ComfyUI" not in p]

            # 清理已加载的模块
            for m in list(sys.modules.keys()):
                if m.startswith("nodes") or m.startswith("lib"):
                    del sys.modules[m]

            import nodes
            assert hasattr(nodes, "NODE_CLASS_MAPPINGS"), "Missing NODE_CLASS_MAPPINGS"
            assert hasattr(nodes, "NODE_DISPLAY_NAME_MAPPINGS"), "Missing NODE_DISPLAY_NAME_MAPPINGS"

            classes = nodes.NODE_CLASS_MAPPINGS
            assert "IYKYKPromptGenerator" in classes
            assert "IYKYKPresetBrowser" in classes
            assert "IYKYKCustomSlotCombiner" in classes
            assert "IYKYKPromptDiagnostics" in classes

            # 测试生成器
            gen = classes["IYKYKPromptGenerator"]()
            pos, neg, desc = gen.generate(prompt_seed=42)
            assert len(pos) > 0, "Generator returned empty positive prompt"

            # 测试预设浏览器
            browser = classes["IYKYKPresetBrowser"]()
            first_preset = nodes._sampler.list_preset_names()[0]
            b_pos, b_neg, b_desc = browser.browse(first_preset, "无 (None)", "高清写真 (High)", prompt_seed=42)
            assert len(b_pos) > 0, "PresetBrowser returned empty prompt"

            # 测试自定义组合
            comb = classes["IYKYKCustomSlotCombiner"]()
            c_pos, c_neg, c_desc = comb.combine(prompt_seed=42, 场景主题="onsen")
            assert len(c_pos) > 0, "CustomSlotCombiner returned empty prompt"

            # 测试诊断节点
            diag = classes["IYKYKPromptDiagnostics"]()
            d_pos, d_neg, d_desc, d_audit = diag.diagnose(prompt_seed=42)
            assert len(d_pos) > 0, "Diagnostics returned empty prompt"
            audit_obj = json.loads(d_audit)
            assert "decisions" in audit_obj, "Invalid diagnostics audit JSON"

            print("   ✅ Successfully verified all 4 ComfyUI nodes in isolated environment.")
        finally:
            sys.path = sys_path_backup


if __name__ == "__main__":
    pack()
