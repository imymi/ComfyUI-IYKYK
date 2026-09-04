"""
errors.py — 统一异常体系定义
包含提示词校验、词法语法错误、数据选择器契约异常、规则配置异常、亲和度配置异常与未消解冲突异常。
"""
from __future__ import annotations

from typing import Any, Optional, Tuple


class PromptValidationError(Exception):
    """提示词装配与预算管理异常。"""
    pass


class PromptSyntaxError(PromptValidationError):
    """词法状态机解析与嵌套语法校验异常。"""
    pass


class DataSelectionError(ValueError):
    """选择器未知显式值或数据未命中异常（Fail-Fast）。"""
    pass


class RuleConfigurationError(RuntimeError):
    """17 规则配置缺失或校验失败异常（Fail-Closed）。"""
    pass


class CatalogIndexingError(RuntimeError):
    """Catalog 索引构建碰撞或非法异常（Fail-Closed）。"""
    pass


class DataLoadError(RuntimeError):
    """当必需的数据文件缺失或 JSON 解析失败时抛出。"""
    pass


class AffinityConfigurationError(RuntimeError):
    """情境亲和度配置缺失、非法或候选与有效词库交集为空时抛出 (Fail-Closed)。"""
    pass


class UnresolvedConflictError(RuntimeError):
    """消解后仍存在残余硬冲突或受保护黑盒冲突时抛出 (Fail-Closed)。"""

    def __init__(
        self,
        message: str = "",
        reason: Optional[str] = None,
        unresolved_conflicts: Tuple[Any, ...] = (),
        report: Optional[Any] = None,
    ):
        full_msg = message or f"Unresolved conflict (reason={reason})"
        super().__init__(full_msg)
        self.reason = reason
        self.unresolved_conflicts = tuple(unresolved_conflicts)
        self.report = report
