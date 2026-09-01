#!/usr/bin/env python3
"""
sync_version.py — 开发辅助脚本：显式同步 pyproject.toml 版本号至 js/version.js
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent


def get_project_version() -> str:
    pyproject = REPO_DIR / "pyproject.toml"
    if not pyproject.is_file():
        raise FileNotFoundError(f"Missing pyproject.toml in {REPO_DIR}")
    content = pyproject.read_text(encoding="utf-8")
    m = re.search(r'version\s*=\s*"([^"]+)"', content)
    if not m:
        raise ValueError("Could not find project.version in pyproject.toml")
    return m.group(1)


def main():
    version = get_project_version()
    js_dir = REPO_DIR / "js"
    js_dir.mkdir(exist_ok=True)
    version_file = js_dir / "version.js"
    content = f'export const EXTENSION_VERSION = "v{version}";\n'
    version_file.write_text(content, encoding="utf-8")
    print(f"✅ Synced version v{version} to js/version.js")


if __name__ == "__main__":
    main()
