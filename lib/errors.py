"""
errors.py — 统一异常体系定义
包含提示词校验、词法语法错误、数据选择器契约异常、规则配置异常、亲和度配置异常与未消解冲突异常。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence, Tuple


CATEGORY_EXTERNAL_OVERRIDE_CONFLICT = "external_override_hard_conflict"
CATEGORY_INVALID_INPUT_TYPE = "invalid_input_type"
CATEGORY_EXTERNAL_BUDGET_EXCEEDED = "external_budget_exceeded"
CATEGORY_SLOT_ALIAS_COLLISION = "slot_alias_collision"
CATEGORY_UNRESOLVED_HARD_CONFLICT = "unresolved_hard_conflict"
CATEGORY_SYNTAX_ERROR = "syntax_error"
CATEGORY_GENERIC_ERROR = "generic_error"


DEFAULT_20_OVERRIDES: Tuple[Dict[str, Any], ...] = tuple(
    {
        "blank_fallback_reason": "omitted",
        "canonical_slot": slot,
        "injected_atom_ids": [],
        "raw_input": None,
        "replaced_target_ids": [],
        "replaced_texts": [],
        "source_port": None,
        "status": "not_provided",
    }
    for slot in (
        "scene_theme", "shot_type", "camera_angle", "character", "nudity",
        "clothing", "lighting", "pose", "expression", "makeup", "hairstyle",
        "jewelry", "imperfections", "tattoo", "props", "liquids", "film",
        "quality", "style_recipe", "custom"
    )
)


class PromptValidationError(Exception):
    """提示词装配与预算管理异常。"""

    def __init__(
        self,
        message: str = "",
        slots: Sequence[str] = (),
        texts: Sequence[str] = (),
        rule_id: Optional[str] = None,
        category: Optional[str] = None,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = str(message)
        self.slots: Tuple[str, ...] = tuple(slots)
        self.texts: Tuple[str, ...] = tuple(texts)
        self.rule_id: Optional[str] = rule_id

        resolved_category = category or self._infer_category(self.message, rule_id)
        self.category: str = resolved_category
        self.error_code: str = error_code or resolved_category
        self.details: Dict[str, Any] = dict(details or {})

    @staticmethod
    def _infer_category(message: str, rule_id: Optional[str]) -> str:
        msg_low = message.lower()
        if "external override conflict" in msg_low or rule_id is not None:
            return CATEGORY_EXTERNAL_OVERRIDE_CONFLICT
        if ("word count" in msg_low and "exceeds" in msg_low) or ("250" in msg_low and "budget" in msg_low):
            return CATEGORY_EXTERNAL_BUDGET_EXCEEDED
        if "conflicting slot" in msg_low or "slot alias" in msg_low or "alias collision" in msg_low or "multiple conflicting" in msg_low:
            return CATEGORY_SLOT_ALIAS_COLLISION
        if "none" in msg_low or "type none" in msg_low or "invalid" in msg_low or "expected str" in msg_low or "disallowed" in msg_low or "unknown" in msg_low or "not allowed" in msg_low:
            return CATEGORY_INVALID_INPUT_TYPE
        return CATEGORY_GENERIC_ERROR

    def to_diagnostics_dict(self, effective_seed: int = 0, overrides: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
        """构建符合 schemas/diagnostics.schema.json 契约的结构化错误报告字典。"""
        overrides_seq = overrides if overrides is not None else DEFAULT_20_OVERRIDES
        overrides_list = []
        for o in overrides_seq:
            if isinstance(o, dict):
                overrides_list.append(dict(o))
            elif hasattr(o, "canonical_slot"):
                overrides_list.append({
                    "blank_fallback_reason": o.blank_fallback_reason,
                    "canonical_slot": o.canonical_slot,
                    "injected_atom_ids": list(o.injected_atom_ids),
                    "raw_input": getattr(o, "raw_input", None),
                    "replaced_target_ids": list(o.replaced_target_ids),
                    "replaced_texts": list(o.replaced_texts),
                    "source_port": o.source_port,
                    "status": o.status,
                })
        return {
            "context_profile": {},
            "counts": {
                "accepted_atoms": 0,
                "budget_filtered": 0,
                "deduplicated": 0,
                "dropped": 0,
                "injected": 0,
                "produced": 0,
                "replaced": 0,
                "source_atoms": 0,
            },
            "decisions": [],
            "effective_seed": int(effective_seed),
            "error_info": {
                "category": self.category,
                "details": (
                    {
                        **self.details,
                        "nudity_constraint": self.nudity_constraint.to_dict(),
                    }
                    if getattr(self, "nudity_constraint", None) is not None
                    and hasattr(self.nudity_constraint, "to_dict")
                    and "nudity_constraint" not in self.details
                    else dict(self.details)
                ),
                "error_code": self.error_code,
                "message": self.message,
                "rule_id": self.rule_id,
                "slots": list(self.slots),
                "texts": list(self.texts),
            },
            "overrides": overrides_list,
            "rules_applied": [],
            "schema_version": "2.0",
            "selections": [],
            "status": "error",
            "unresolved_conflicts": [],
        }

    def to_diagnostics_json(self, effective_seed: int = 0, overrides: Optional[Sequence[Any]] = None) -> str:
        """确定性序列化为紧凑 UTF-8 错误审计报告 JSON 字符串。"""
        d = self.to_diagnostics_dict(effective_seed, overrides=overrides)
        return json.dumps(
            d,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )



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
        overrides: Optional[Sequence[Any]] = None,
    ):
        full_msg = message or f"Unresolved conflict (reason={reason})"
        super().__init__(full_msg)
        self.reason = reason
        self.unresolved_conflicts = tuple(unresolved_conflicts)
        self.report = report
        self.overrides = tuple(overrides) if overrides else ()
        self.category = CATEGORY_UNRESOLVED_HARD_CONFLICT
        self.error_code = str(reason or CATEGORY_UNRESOLVED_HARD_CONFLICT)

    def to_diagnostics_dict(self, effective_seed: int = 0, overrides: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
        """构建符合 schemas/diagnostics.schema.json 契约的结构化错误报告字典。"""
        unres = [list(c) if isinstance(c, (list, tuple)) else c for c in self.unresolved_conflicts]
        overrides_seq = overrides if overrides is not None else (self.overrides or DEFAULT_20_OVERRIDES)
        overrides_list = []
        for o in overrides_seq:
            if isinstance(o, dict):
                overrides_list.append(dict(o))
            elif hasattr(o, "canonical_slot"):
                overrides_list.append({
                    "blank_fallback_reason": o.blank_fallback_reason,
                    "canonical_slot": o.canonical_slot,
                    "injected_atom_ids": list(o.injected_atom_ids),
                    "raw_input": o.raw_input,
                    "replaced_target_ids": list(o.replaced_target_ids),
                    "replaced_texts": list(o.replaced_texts),
                    "source_port": o.source_port,
                    "status": o.status,
                })
        return {
            "context_profile": {},
            "counts": {
                "accepted_atoms": 0,
                "budget_filtered": 0,
                "deduplicated": 0,
                "dropped": 0,
                "injected": 0,
                "produced": 0,
                "replaced": 0,
                "source_atoms": 0,
            },
            "decisions": [],
            "effective_seed": int(effective_seed),
            "error_info": {
                "category": self.category,
                "details": {"reason": self.reason},
                "error_code": self.error_code,
                "message": str(self),
                "rule_id": None,
                "slots": [],
                "texts": [],
            },
            "overrides": overrides_list,
            "rules_applied": [],
            "schema_version": "2.0",
            "selections": [],
            "status": "error",
            "unresolved_conflicts": unres,
        }

    def to_diagnostics_json(self, effective_seed: int = 0) -> str:
        d = self.to_diagnostics_dict(effective_seed)
        return json.dumps(
            d,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class UnresolvedEnsembleRelationError(RuntimeError):
    """多件套构件关系消歧失败或存在环路异常 (Fail-Closed, 阻断迁移与入库)。"""
    pass


class QuarantineLeakageError(RuntimeError):
    """隔离项泄漏异常：试图将处于物理隔离池中的条目载入生产运行时 (Fail-Closed)。"""
    pass
