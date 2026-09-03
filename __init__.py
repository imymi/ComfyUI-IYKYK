"""
ComfyUI-IYKYK — 一键提示词生成与冲突消解插件

基于 nsfw-prompt-templates-asian 词库规范，
提供 18 核心 + 2 辅助槽位流水线、17 大冲突消解引擎、77 预设模板。
"""
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

WEB_DIRECTORY = "./js"
