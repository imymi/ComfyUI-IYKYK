"""
conflict_resolver.py — 冲突检测与自动消解引擎

严格实现 nsfw-prompt-templates-asian 项目定义的全部规则与限制：
1. 裸露×内衣状态互斥消解（私处暴露时内裤清除，全裸时清除穿着词）
2. 材质穿透伪影消解（禁止 sheer/see-through，转为物理状态）
3. 视线×镜头角度几何匹配（仰拍必下看，俯拍必上看，POV必对视）
4. 视线方向唯一性（禁止同时对视与回避）
5. 液体微量与安全法则（禁止闭眼精液白内障效果，强制微量词）
6. 设备×画质等级兼容（监控/手机不匹配8K专业写真词）
7. 纹身 6 词真皮层融合（彻底解决贴纸漂浮感）
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConflictResolver:
    """检测并自动修正提示词冲突与限制。"""

    def __init__(self, data_dir: Optional[str | Path] = None):
        self._data_dir = Path(data_dir) if data_dir else None
        self._rules: Optional[Dict[str, Any]] = None

    def _load_rules(self) -> Dict[str, Any]:
        if self._rules is None:
            if self._data_dir:
                p = self._data_dir / "conflict_rules.json"
                if p.is_file():
                    self._rules = json.loads(p.read_text(encoding="utf-8"))
                    return self._rules
            self._rules = {"rules": []}
        return self._rules

    def resolve(self, slots: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """执行完整冲突消解流水线。"""
        rules_data = self._load_rules()
        rules = rules_data.get("rules", [])

        # 1. 裸露与内衣状态互斥
        self._resolve_nudity_clothing(slots, rules)

        # 2. 材质穿透伪影消解
        self._resolve_material_penetration(slots, rules)

        # 3. 视线与镜头角度几何匹配
        self._resolve_gaze_angle(slots, rules)

        # 4. 视线方向互斥
        self._resolve_gaze_mutual_exclusion(slots, rules)

        # 5. 液体微量与安全法则
        self._resolve_liquids(slots, rules)

        # 6. 设备与画质兼容
        self._resolve_device_quality(slots, rules)

        # 7. 纹身真皮层融合
        self._resolve_tattoo_fusion(slots, rules)

        return slots

    # ─── 内部辅助工具 ───

    @staticmethod
    def _all_text(slots: Dict[str, List[str]]) -> str:
        parts = []
        for tags in slots.values():
            parts.extend(tags)
        return ", ".join(parts).lower()

    @staticmethod
    def _remove_matching(slots: Dict[str, List[str]], banned_substring: str):
        banned = banned_substring.lower()
        for key in list(slots.keys()):
            slots[key] = [t for t in slots[key] if banned not in t.lower()]

    @staticmethod
    def _add_unique(slots: Dict[str, List[str]], slot_name: str, tag: str):
        if slot_name not in slots:
            slots[slot_name] = []
        if tag.lower() not in [t.lower() for t in slots[slot_name]]:
            slots[slot_name].append(tag)

    # ─── 规则 1: 裸露与内衣/衣物状态互斥 ───

    def _resolve_nudity_clothing(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "nudity_clothing_conflicts"), None)
        all_txt = self._all_text(slots)

        conflicts = rule.get("conflicts", []) if rule else [
            {"trigger": ["pussy visible", "exposed vagina", "spread pussy"], "ban": ["panties showing", "wearing panties", "wearing underwear"]},
            {"trigger": ["topless", "bare breasts"], "ban": ["wearing bra", "bra on"]},
            {"trigger": ["completely naked", "full nude"], "ban": ["wearing blazer", "wearing skirt", "wearing dress"]},
            {"trigger": ["no panties", "pussy visible"], "ban": ["cameltoe"]},
        ]

        for item in conflicts:
            triggers = item.get("trigger", [])
            bans = item.get("ban", [])
            if any(tr.lower() in all_txt for tr in triggers):
                for b in bans:
                    self._remove_matching(slots, b)

    # ─── 规则 2: 材质穿透伪影消解 ───

    def _resolve_material_penetration(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "material_penetration"), None)
        banned_words = rule.get("banned_words", ["sheer", "see-through", "transparent fabric"]) if rule else ["sheer", "see-through", "transparent"]
        replacements = rule.get("replacements", ["unbuttoned", "lifted up", "slipping off shoulder", "wet dress clinging tightly to skin"]) if rule else ["unbuttoned", "lifted up"]

        all_txt = self._all_text(slots)
        had_banned = False
        for word in banned_words:
            if word.lower() in all_txt:
                self._remove_matching(slots, word)
                had_banned = True

        if had_banned and replacements:
            self._add_unique(slots, "clothing", random.choice(replacements))

    # ─── 规则 3: 视线与镜头角度几何匹配 ───

    def _resolve_gaze_angle(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "gaze_angle_geometry"), None)
        mappings = rule.get("mappings", []) if rule else [
            {"angles": ["low angle", "from below", "worm eye view"], "required_gaze": "looking down at camera", "banned_gaze": ["looking up"]},
            {"angles": ["high angle", "from above", "overhead", "bird eye view"], "required_gaze": "looking up at camera", "banned_gaze": ["looking down"]},
            {"angles": ["point of view", "pov"], "required_gaze": "direct eye contact with camera", "banned_gaze": []},
        ]

        all_txt = self._all_text(slots)
        for m in mappings:
            angles = m.get("angles", [])
            req = m.get("required_gaze", "")
            bans = m.get("banned_gaze", [])

            if any(a.lower() in all_txt for a in angles):
                for b in bans:
                    self._remove_matching(slots, b)
                if req and req.lower() not in all_txt:
                    self._add_unique(slots, "expression", req)

    # ─── 规则 4: 视线方向互斥 ───

    def _resolve_gaze_mutual_exclusion(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "gaze_mutual_exclusion"), None)
        pairs = rule.get("exclusive_pairs", [["direct eye contact", "looking away"]]) if rule else [["direct eye contact", "looking away"]]

        all_txt = self._all_text(slots)
        for p in pairs:
            if len(p) >= 2 and p[0].lower() in all_txt and p[1].lower() in all_txt:
                # 优先保留 direct eye contact
                self._remove_matching(slots, p[1])

    # ─── 规则 5: 液体微量与安全法则 ───

    def _resolve_liquids(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "liquid_restrictions"), None)
        banned_combos = rule.get("banned_combos", []) if rule else []

        # 检查并替换闭眼精液白内障伪影等
        for key in list(slots.keys()):
            for i, tag in enumerate(slots[key]):
                for item in banned_combos:
                    for tr in item.get("trigger", []):
                        if tr.lower() in tag.lower():
                            slots[key][i] = tag.lower().replace(tr.lower(), item.get("replace", ""))

        # 液体微量修饰词
        all_txt = self._all_text(slots)
        liquid_words = ["cum", "semen", "saliva", "drool", "pussy juice", "breast milk"]
        modifiers = ["single drop of", "thin streak of", "faint trace of", "few drops of", "glistening beads of"]

        has_liquid = any(lw in all_txt for lw in liquid_words)
        has_mod = any(m in all_txt for m in modifiers)

        if has_liquid and not has_mod:
            mod = random.choice(modifiers)
            for key in ["liquids", "props", "imperfections"]:
                if key in slots and slots[key]:
                    for i, tag in enumerate(slots[key]):
                        if any(lw in tag.lower() for lw in liquid_words):
                            slots[key][i] = f"{mod} {tag}"
                            break

    # ─── 规则 6: 设备与画质兼容 ───

    def _resolve_device_quality(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "device_quality_compatibility"), None)
        constraints = rule.get("device_constraints", []) if rule else []

        all_txt = self._all_text(slots)
        for c in constraints:
            devices = c.get("devices", [])
            banned_tags = c.get("banned_tags", [])
            if any(d.lower() in all_txt for d in devices):
                for bt in banned_tags:
                    self._remove_matching(slots, bt)

    # ─── 规则 7: 纹身真皮层融合 ───

    def _resolve_tattoo_fusion(self, slots: Dict[str, List[str]], rules: List[Dict]):
        all_txt = self._all_text(slots)
        tattoo_indicators = ["tattoo", "tattooed", "ink", "irezumi", "tally marks", "crest ink"]

        has_tattoo = any(ind in all_txt for ind in tattoo_indicators)
        if has_tattoo:
            fusion_words = [
                "realistic tattoo",
                "ink embedded in dermis",
                "tattoo beneath skin surface",
                "follows body contours",
                "slightly faded edges",
                "pores visible through ink",
            ]
            for fw in fusion_words:
                if fw.lower() not in all_txt:
                    self._add_unique(slots, "tattoo", fw)


def sanitize_prompt(prompt: str) -> str:
    """清理最终 prompt 中的格式问题。"""
    prompt = re.sub(r",\s*,+", ",", prompt)
    prompt = prompt.strip(", \n\t")
    prompt = re.sub(r"\s{2,}", " ", prompt)
    prompt = re.sub(r",(\S)", r", \1", prompt)
    return prompt
