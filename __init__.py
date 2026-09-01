"""
ComfyUI-IYKYK — 一键提示词生成插件

基于 nsfw-prompt-templates-asian 和 AmazingDraw 项目的明文数据，
提供 15 槽位装配流水线、冲突检测、77 预设模板。
"""
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

WEB_DIRECTORY = None
