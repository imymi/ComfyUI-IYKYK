"""
conflict_resolver.py — 提示词冲突检测与消解引擎

严格实现 8 大多规则冲突消解：
1. 裸露与内衣/衣物状态互斥（私处暴露自动剔除内裤，全裸自动剔除穿着描述）
2. 材质穿透伪影消解（剔除 sheer/see-through 等崩图词，替换为真实物理脱法）
3. 视线与镜头角度几何匹配（仰拍强制俯视、俯拍强制仰视、POV 强制直视）
4. 视线方向唯一性（消解直视与移开视线互斥）
5. 液体微量法则与安全渲染（自动添加微量量词，拦截闭眼精液白内障）
6. 设备与画质等级兼容（监控/手机自拍自动过滤 8k/单反/写真标签）
7. 纹身 6 词真皮层融合（自动绑定真皮层物理融合词）
8. 空间与环境自洽互斥（禁止多个独立场所/室内外冲突并存，如温泉与餐厅/街头屋台并存、野外草丛与室内房间并存）
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ConflictResolver:
    """提示词冲突检测与消解引擎。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._rules_cache: Optional[List[Dict[str, Any]]] = None

    def _load_rules(self) -> List[Dict[str, Any]]:
        if self._rules_cache is None:
            p = self.data_dir / "conflict_rules.json"
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                self._rules_cache = data.get("rules", [])
            else:
                self._rules_cache = []
        return self._rules_cache

    def resolve(self, slots: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """
        对 15 槽位已采样的 tags 执行多遍冲突检测与修正。
        """
        rules = self._load_rules()

        # 1. 空间与环境自洽互斥（最优先：先确定物理空间自洽）
        self._resolve_spatial_environment(slots, rules)

        # 2. 裸露与内衣状态互斥
        self._resolve_nudity_clothing(slots, rules)

        # 3. 材质穿透伪影消解
        self._resolve_material_penetration(slots, rules)

        # 4. 视线与镜头角度几何匹配
        self._resolve_gaze_angle(slots, rules)

        # 5. 视线方向互斥消解
        self._resolve_gaze_mutual_exclusion(slots, rules)

        # 6. 液体微量与安全法则
        self._resolve_liquids(slots, rules)

        # 7. 设备与画质兼容
        self._resolve_device_quality(slots, rules)

        # 8. 纹身真皮层融合
        self._resolve_tattoo_fusion(slots, rules)

        return slots

    def _all_text(self, slots: Dict[str, List[str]]) -> str:
        parts = []
        for tags in slots.values():
            if isinstance(tags, list):
                parts.extend([str(t).lower() for t in tags])
        return " ".join(parts)

    def _remove_matching(self, slots: Dict[str, List[str]], banned_substring: str):
        banned = banned_substring.lower().strip()
        if not banned:
            return
        for key in list(slots.keys()):
            slots[key] = [t for t in slots[key] if banned not in t.lower()]

    @staticmethod
    def _add_unique(slots: Dict[str, List[str]], slot_name: str, tag: str):
        if slot_name not in slots:
            slots[slot_name] = []
        if tag.lower() not in [t.lower() for t in slots[slot_name]]:
            slots[slot_name].append(tag)

    # ─── 规则 1: 空间与环境自洽互斥 ───

    def _resolve_spatial_environment(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "spatial_environmental_mutual_exclusion"), None)
        deprecated_map = rule.get("deprecated_tags", {}) if rule else {"spinning room": "drunken stupor"}

        # 1. 替换废弃或有歧义的词条（如 spinning room -> drunken stupor）
        for key in list(slots.keys()):
            for i, tag in enumerate(slots[key]):
                for old_w, new_w in deprecated_map.items():
                    if old_w.lower() in tag.lower():
                        slots[key][i] = re.sub(rf"\b{re.escape(old_w)}\b", new_w, tag, flags=re.IGNORECASE)

        # 2. 场所集群互斥（温泉 vs 餐饮/包厢 vs 校园 vs 职场 vs 交通工具 vs 纯野外）
        venue_clusters = {
            "onsen": [
                "onsen", "hot spring", "rotenburo", "ryokan bath", "public bath", "sento", "sauna", "jacuzzi", "soapland bath"
            ],
            "dining": [
                "cafe booth", "coffee shop", "yatai stall", "street food cart", "ramen shop", "izakaya", "bar counter", "love hotel restaurant", "food stall with curtain"
            ],
            "school": [
                "classroom", "blackboard", "student desk", "teacher desk", "school library", "gym storage", "infirmary"
            ],
            "office": [
                "office cubicle", "conference room", "executive desk", "office elevator", "break room", "corporate office"
            ],
            "transport": [
                "subway car", "train seat", "train door", "train interior", "airplane cabin", "car backseat", "bus interior", "shinkansen"
            ],
            "outdoor": [
                "riverbank", "embankment", "behind bushes", "under bridge", "park at night", "beach at night", "forest clearing", "mountain trail", "seaside cave"
            ],
            "bedroom": [
                "bedroom", "love hotel room", "tatami futon", "messy bed", "hotel room bed"
            ],
        }

        # 收集所有已出现的场所标签位置
        active_venues = []
        for vname, vtags in venue_clusters.items():
            for key in ["scene_theme", "character", "props"]:
                for t in slots.get(key, []):
                    if any(vt in t.lower() for vt in vtags):
                        active_venues.append(vname)
                        break

        # 如果同时激活了多个大类场所，以 scene_theme 中最早出现的场所为准
        if len(set(active_venues)) > 1:
            dominant_venue = active_venues[0]
            banned_venues = [v for v in set(active_venues) if v != dominant_venue]
            banned_tags_all = []
            for bv in banned_venues:
                banned_tags_all.extend(venue_clusters[bv])
            for b in banned_tags_all:
                self._remove_matching(slots, b)

        # 3. 餐饮子场所去重（如果出现多个餐饮细分点，保留第一个）
        dining_tags = venue_clusters["dining"]
        found_dining = []
        for key in list(slots.keys()):
            for t in slots[key]:
                if any(dt in t.lower() for dt in dining_tags):
                    found_dining.append(t)
        if len(found_dining) > 1:
            first_dining = found_dining[0]
            for extra in found_dining[1:]:
                self._remove_matching(slots, extra)

        # 4. 室内外物理互斥
        outdoor_exclusive = [
            "outdoor bath", "rotenburo", "open-air bath", "onsen with snow view", "snow view",
            "riverbank", "embankment", "under bridge", "behind bushes", "park at night",
            "beach at night", "forest clearing", "mountain trail", "seaside cave", "outdoor hot spring"
        ]
        indoor_exclusive = [
            "indoor onsen", "private onsen", "onsen changing room", "changing room", "locker room",
            "bathroom", "shower room", "bedroom", "living room", "kitchen", "office cubicle",
            "classroom", "cafe booth", "elevator", "dressing room", "shower stall", "spinning room"
        ]

        all_txt = self._all_text(slots)
        has_outdoor = any(m in all_txt for m in outdoor_exclusive)
        has_indoor = any(m in all_txt for m in indoor_exclusive)

        if has_outdoor and has_indoor:
            # 检查 scene_theme 中的倾向
            scene_txt = " ".join(slots.get("scene_theme", [])).lower()
            if any(m in scene_txt for m in outdoor_exclusive):
                # 确定为室外场景：清理室内标签
                for item in indoor_exclusive:
                    self._remove_matching(slots, item)
            else:
                # 确定为室内场景：清理室外标签
                for item in outdoor_exclusive:
                    self._remove_matching(slots, item)

        # 5. 温泉内部子空间细化互斥
        all_txt = self._all_text(slots)
        if "indoor onsen" in all_txt or "private onsen" in all_txt:
            for b in ["outdoor bath", "rotenburo", "open-air bath", "snow view", "changing room", "locker room"]:
                self._remove_matching(slots, b)
        elif any(k in all_txt for k in ["outdoor bath", "rotenburo", "open-air bath", "snow view"]):
            for b in ["indoor onsen", "changing room", "locker room", "indoor bath"]:
                self._remove_matching(slots, b)
        elif "changing room" in all_txt or "locker room" in all_txt:
            for b in ["indoor onsen", "outdoor bath", "rotenburo", "snow view", "soaking in tub", "steaming water"]:
                self._remove_matching(slots, b)

    # ─── 规则 2: 裸露与内衣/衣物状态互斥 ───

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

    # ─── 规则 3: 材质穿透伪影消解 ───

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

    # ─── 规则 4: 视线与镜头角度几何匹配 ───

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

    # ─── 规则 5: 视线方向互斥 ───

    def _resolve_gaze_mutual_exclusion(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "gaze_mutual_exclusion"), None)
        pairs = rule.get("exclusive_pairs", [["direct eye contact", "looking away"]]) if rule else [["direct eye contact", "looking away"]]

        all_txt = self._all_text(slots)
        for p in pairs:
            if len(p) >= 2 and p[0].lower() in all_txt and p[1].lower() in all_txt:
                self._remove_matching(slots, p[1])

    # ─── 规则 6: 液体微量与安全法则 ───

    def _resolve_liquids(self, slots: Dict[str, List[str]], rules: List[Dict]):
        rule = next((r for r in rules if r.get("id") == "liquid_restrictions"), None)
        banned_combos = rule.get("banned_combos", []) if rule else []

        for key in list(slots.keys()):
            for i, tag in enumerate(slots[key]):
                for item in banned_combos:
                    for tr in item.get("trigger", []):
                        if tr.lower() in tag.lower():
                            slots[key][i] = tag.lower().replace(tr.lower(), item.get("replace", ""))

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

    # ─── 规则 7: 设备与画质兼容 ───

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

    # ─── 规则 8: 纹身真皮层融合 ───

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
    # 替换 deprecated 词条
    prompt = re.sub(r"\bspinning room\b", "drunken stupor", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r",\s*,+", ",", prompt)
    prompt = prompt.strip(", \n\t")
    prompt = re.sub(r"\s{2,}", " ", prompt)
    prompt = re.sub(r",(\S)", r", \1", prompt)
    return prompt
