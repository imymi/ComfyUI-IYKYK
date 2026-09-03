"""
runtime_manifest.py — 单一事实来源：ComfyUI-IYKYK 运行时必需文件清单 (严格 38 个文件)
"""
from __future__ import annotations

from typing import Tuple

RUNTIME_ROOT_FILES: Tuple[str, ...] = (
    ".comfyignore",
    "CHANGELOG.md",
    "README.md",
    "__init__.py",
    "nodes.py",
    "pyproject.toml",
)

RUNTIME_LIB_FILES: Tuple[str, ...] = (
    "__init__.py",
    "assembler.py",
    "atomizer.py",
    "conflict_resolver.py",
    "errors.py",
    "lexer.py",
    "models.py",
    "rule_contract.py",
    "runtime_manifest.py",
    "sampler.py",
    "slot_contract.py",
)

RUNTIME_JS_FILES: Tuple[str, ...] = (
    "iykyk_ui.js",
    "version.js",
)

RUNTIME_DATA_FILES: Tuple[str, ...] = (
    "accessories.json",
    "characters.json",
    "clothing.json",
    "conflict_rules.json",
    "expressions.json",
    "film_stocks.json",
    "imperfections.json",
    "lighting.json",
    "makeup.json",
    "negative_prompts.json",
    "nudity_levels.json",
    "poses.json",
    "presets.json",
    "props.json",
    "scenes.json",
    "shot_types.json",
    "style_recipes.json",
    "tattoos.json",
    "themes.json",
)

# 严格 38 个运行时文件列表 (相对仓库根目录路径)
RUNTIME_PACKAGE_FILES: Tuple[str, ...] = tuple(
    sorted(
        list(RUNTIME_ROOT_FILES)
        + [f"lib/{f}" for f in RUNTIME_LIB_FILES]
        + [f"js/{f}" for f in RUNTIME_JS_FILES]
        + [f"data/{f}" for f in RUNTIME_DATA_FILES]
    )
)

TOTAL_RUNTIME_FILES_COUNT: int = len(RUNTIME_PACKAGE_FILES)  # 严格为 38
