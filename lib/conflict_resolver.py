"""
conflict_resolver.py — 结构化提示词冲突检测与消解引擎

严格实现 8 大多规则冲突消解：
1. 裸露与内衣/衣物状态互斥（私处暴露自动剔除内裤，全裸自动剔除穿着描述）
2. 材质穿透伪影消解（剔除 sheer/see-through 等崩图词，替换为真实物理脱法）
3. 视线与镜头角度几何匹配（仰拍强制俯视、俯拍强制仰视、POV 强制直视）
4. 视线方向唯一性（消解直视与移开视线互斥）
5. 液体微量法则与安全渲染（自动添加微量量词，拦截闭眼精液白内障）
6. 设备与画质等级兼容（监控/手机自拍自动过滤 8k/单反/写真标签）
7. 纹身真皮层融合（仅针对纹身槽位或显式纹身词条，杜绝 pink/drink/link 误触发）
8. 空间与环境自洽互斥（按片段顺序锁定主场所，禁止多个独立场所/室内外冲突并存）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence

from .models import PromptFragment


class ConflictResolver:
    """基于 PromptFragment 的结构化提示词冲突检测与消解引擎。"""

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

    def resolve_fragments(
        self,
        fragments: Sequence[PromptFragment],
        rng: Optional[Random] = None
    ) -> List[PromptFragment]:
        """
        核心方法：对结构化 PromptFragment 列表执行 8 大冲突消解。
        """
        if rng is None:
            rng = Random(42)

        frags = list(fragments)
        rules = self._load_rules()

        # 1. 空间与环境自洽互斥
        frags = self._resolve_spatial_fragments(frags, rules, rng)

        # 2. 裸露与内衣状态互斥
        frags = self._resolve_nudity_clothing_fragments(frags, rules, rng)

        # 3. 材质穿透伪影消解
        frags = self._resolve_material_penetration_fragments(frags, rules, rng)

        # 4. 视线与镜头角度几何匹配
        frags = self._resolve_gaze_angle_fragments(frags, rules, rng)

        # 5. 视线方向互斥消解
        frags = self._resolve_gaze_mutual_exclusion_fragments(frags, rules, rng)

        # 6. 液体微量与安全法则
        frags = self._resolve_liquids_fragments(frags, rules, rng)

        # 7. 设备与画质兼容
        frags = self._resolve_device_quality_fragments(frags, rules, rng)

        # 8. 纹身真皮层融合（严格基于纹身槽位或显式纹身标记）
        frags = self._resolve_tattoo_fusion_fragments(frags, rules, rng)

        return frags

    def resolve(
        self,
        slots: Dict[str, List[str]],
        rng: Optional[Random] = None
    ) -> Dict[str, List[str]]:
        """
        兼容接口：接收槽位字典并返回消解后的槽位字典。
        """
        if rng is None:
            rng = Random(42)

        fragments: List[PromptFragment] = []
        order = 0
        for slot_name, tags in slots.items():
            if isinstance(tags, list):
                for t in tags:
                    if str(t).strip():
                        fragments.append(
                            PromptFragment(
                                text=str(t).strip(),
                                source_slot=slot_name,
                                order=order,
                            )
                        )
                        order += 1

        resolved_frags = self.resolve_fragments(fragments, rng)

        # 重建 slots 字典
        new_slots: Dict[str, List[str]] = {k: [] for k in slots.keys()}
        for f in resolved_frags:
            if f.source_slot not in new_slots:
                new_slots[f.source_slot] = []
            new_slots[f.source_slot].append(f.text)

        return new_slots

    # ─── 规则 1: 空间与环境自洽互斥 ───

    def _resolve_spatial_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "spatial_environmental_mutual_exclusion"), None)
        deprecated_map = rule.get("deprecated_tags", {}) if rule else {"spinning room": "drunken stupor"}

        # 0. 优先读取 exclusive_group 结构化互斥
        groups_by_order: List[str] = []
        for f in frags:
            if f.exclusive_group and f.exclusive_group not in groups_by_order:
                groups_by_order.append(f.exclusive_group)

        if len(groups_by_order) > 1:
            dominant_group = groups_by_order[0]
            frags = [f for f in frags if f.exclusive_group is None or f.exclusive_group == dominant_group]

        # 1. 替换废弃或歧义词条
        cleaned_frags: List[PromptFragment] = []
        for f in frags:
            txt = f.text
            for old_w, new_w in deprecated_map.items():
                if re.search(rf"\b{re.escape(old_w)}\b", txt, flags=re.IGNORECASE):
                    txt = re.sub(rf"\b{re.escape(old_w)}\b", new_w, txt, flags=re.IGNORECASE)
            cleaned_frags.append(
                PromptFragment(
                    text=txt,
                    source_slot=f.source_slot,
                    source_item_id=f.source_item_id,
                    context_ids=f.context_ids,
                    order=f.order,
                )
            )
        frags = cleaned_frags

        # 2. 场所集群互斥（按 order 确定首个声明的主场所）
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

        # 扫描出现的场所及其首现 order
        active_venues: List[Tuple[int, str]] = []
        for vname, vtags in venue_clusters.items():
            for f in frags:
                if any(re.search(rf"\b{re.escape(vt)}\b", f.text, flags=re.IGNORECASE) or vt.lower() in f.text.lower() for vt in vtags):
                    active_venues.append((f.order, vname))
                    break

        if len(set(v for _, v in active_venues)) > 1:
            active_venues.sort(key=lambda x: x[0])
            dominant_venue = active_venues[0][1]
            banned_venues = [v for _, v in active_venues if v != dominant_venue]
            banned_tags_all: List[str] = []
            for bv in banned_venues:
                banned_tags_all.extend(venue_clusters[bv])

            frags = [
                f for f in frags
                if not any(bt.lower() in f.text.lower() for bt in banned_tags_all)
            ]

        # 3. 餐饮细分子场所去重（如果出现多个餐饮点，保留第一个）
        dining_tags = venue_clusters["dining"]
        found_dining_indices = [
            i for i, f in enumerate(frags)
            if any(dt.lower() in f.text.lower() for dt in dining_tags)
        ]
        if len(found_dining_indices) > 1:
            keep_idx = found_dining_indices[0]
            drop_indices = set(found_dining_indices[1:])
            frags = [f for i, f in enumerate(frags) if i not in drop_indices]

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

        has_outdoor = any(any(m.lower() in f.text.lower() for m in outdoor_exclusive) for f in frags)
        has_indoor = any(any(m.lower() in f.text.lower() for m in indoor_exclusive) for f in frags)

        if has_outdoor and has_indoor:
            # 找到首个声明是室外还是室内
            first_outdoor_order = min((f.order for f in frags if any(m.lower() in f.text.lower() for m in outdoor_exclusive)), default=9999)
            first_indoor_order = min((f.order for f in frags if any(m.lower() in f.text.lower() for m in indoor_exclusive)), default=9999)

            if first_outdoor_order < first_indoor_order:
                # 室外为主：剔除室内冲突项
                frags = [f for f in frags if not any(m.lower() in f.text.lower() for m in indoor_exclusive)]
            else:
                # 室内为主：剔除室外冲突项
                frags = [f for f in frags if not any(m.lower() in f.text.lower() for m in outdoor_exclusive)]

        # 5. 温泉内部细分子空间互斥
        all_text = " ".join(f.text.lower() for f in frags)
        if "indoor onsen" in all_text or "private onsen" in all_text:
            frags = [
                f for f in frags
                if not any(b.lower() in f.text.lower() for b in ["outdoor bath", "rotenburo", "open-air bath", "snow view", "changing room", "locker room"])
            ]
        elif any(k.lower() in all_text for k in ["outdoor bath", "rotenburo", "open-air bath", "snow view"]):
            frags = [
                f for f in frags
                if not any(b.lower() in f.text.lower() for b in ["indoor onsen", "changing room", "locker room", "indoor bath"])
            ]
        elif "changing room" in all_text or "locker room" in all_text:
            frags = [
                f for f in frags
                if not any(b.lower() in f.text.lower() for b in ["indoor onsen", "outdoor bath", "rotenburo", "snow view", "soaking in tub", "steaming water"])
            ]

        return frags

    # ─── 规则 2: 裸露与内衣状态互斥 ───

    def _resolve_nudity_clothing_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "nudity_clothing_conflicts"), None)
        all_text = " ".join(f.text.lower() for f in frags)

        conflicts = rule.get("conflicts", []) if rule else [
            {"trigger": ["pussy visible", "exposed vagina", "spread pussy"], "ban": ["panties showing", "wearing panties", "wearing underwear"]},
            {"trigger": ["topless", "bare breasts"], "ban": ["wearing bra", "bra on"]},
            {"trigger": ["completely naked", "full nude"], "ban": ["wearing blazer", "wearing skirt", "wearing dress"]},
            {"trigger": ["no panties", "pussy visible"], "ban": ["cameltoe"]},
        ]

        banned_set: set[str] = set()
        for item in conflicts:
            triggers = item.get("trigger", [])
            bans = item.get("ban", [])
            if any(tr.lower() in all_text for tr in triggers):
                for b in bans:
                    banned_set.add(b.lower())

        if banned_set:
            frags = [
                f for f in frags
                if not any(b in f.text.lower() for b in banned_set)
            ]
        return frags

    # ─── 规则 3: 材质穿透伪影消解 ───

    def _resolve_material_penetration_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "material_penetration"), None)
        banned_words = rule.get("banned_words", ["sheer", "see-through", "transparent fabric"]) if rule else ["sheer", "see-through", "transparent"]
        replacements = rule.get("replacements", ["unbuttoned", "lifted up", "slipping off shoulder", "wet dress clinging tightly to skin"]) if rule else ["unbuttoned", "lifted up"]

        had_banned = False
        cleaned: List[PromptFragment] = []
        for f in frags:
            txt = f.text
            for bw in banned_words:
                if re.search(rf"\b{re.escape(bw)}\b", txt, flags=re.IGNORECASE):
                    txt = re.sub(rf"\b{re.escape(bw)}\b", "", txt, flags=re.IGNORECASE).strip()
                    had_banned = True
            if txt:
                cleaned.append(
                    PromptFragment(
                        text=txt,
                        source_slot=f.source_slot,
                        source_item_id=f.source_item_id,
                        context_ids=f.context_ids,
                        order=f.order,
                    )
                )

        if had_banned and replacements:
            rep = rng.choice(replacements)
            cleaned.append(
                PromptFragment(
                    text=rep,
                    source_slot="clothing",
                    order=max((f.order for f in cleaned), default=0) + 1,
                )
            )

        return cleaned

    # ─── 规则 4: 视线与镜头角度几何匹配 ───

    def _resolve_gaze_angle_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "gaze_angle_geometry"), None)
        mappings = rule.get("mappings", []) if rule else [
            {"angles": ["low angle", "from below", "worm eye view"], "required_gaze": "looking down at camera", "banned_gaze": ["looking up"]},
            {"angles": ["high angle", "from above", "overhead", "bird eye view"], "required_gaze": "looking up at camera", "banned_gaze": ["looking down"]},
            {"angles": ["point of view", "pov"], "required_gaze": "direct eye contact with camera", "banned_gaze": []},
        ]

        all_text = " ".join(f.text.lower() for f in frags)
        banned_set: set[str] = set()
        required_list: List[str] = []

        for m in mappings:
            angles = m.get("angles", [])
            req = m.get("required_gaze", "")
            bans = m.get("banned_gaze", [])
            if any(a.lower() in all_text for a in angles):
                for b in bans:
                    banned_set.add(b.lower())
                if req and req.lower() not in all_text:
                    required_list.append(req)

        if banned_set:
            frags = [f for f in frags if not any(b in f.text.lower() for b in banned_set)]

        for req in required_list:
            frags.append(
                PromptFragment(
                    text=req,
                    source_slot="expression",
                    order=max((f.order for f in frags), default=0) + 1,
                )
            )

        return frags

    # ─── 规则 5: 视线方向互斥 ───

    def _resolve_gaze_mutual_exclusion_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "gaze_mutual_exclusion"), None)
        pairs = rule.get("exclusive_pairs", [["direct eye contact", "looking away"]]) if rule else [["direct eye contact", "looking away"]]

        all_text = " ".join(f.text.lower() for f in frags)
        for p in pairs:
            if len(p) >= 2 and p[0].lower() in all_text and p[1].lower() in all_text:
                # 保留前者，剔除后者
                frags = [f for f in frags if p[1].lower() not in f.text.lower()]

        return frags

    # ─── 规则 6: 液体微量与安全法则 ───

    def _resolve_liquids_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "liquid_restrictions"), None)
        liquid_words = ["cum", "semen", "saliva", "drool", "pussy juice", "breast milk"]
        modifiers = ["single drop of", "thin streak of", "faint trace of", "few drops of", "glistening beads of"]

        all_text = " ".join(f.text.lower() for f in frags)
        has_liquid = any(re.search(rf"\b{re.escape(lw)}\b", all_text, flags=re.IGNORECASE) for lw in liquid_words)
        has_mod = any(m in all_text for m in modifiers)

        if has_liquid and not has_mod:
            mod = rng.choice(modifiers)
            modified = False
            result: List[PromptFragment] = []
            for f in frags:
                if not modified and f.source_slot in ("liquids", "props", "imperfections") and any(re.search(rf"\b{re.escape(lw)}\b", f.text, flags=re.IGNORECASE) for lw in liquid_words):
                    result.append(
                        PromptFragment(
                            text=f"{mod} {f.text}",
                            source_slot=f.source_slot,
                            source_item_id=f.source_item_id,
                            context_ids=f.context_ids,
                            order=f.order,
                        )
                    )
                    modified = True
                else:
                    result.append(f)
            frags = result

        return frags

    # ─── 规则 7: 设备与画质兼容 ───

    def _resolve_device_quality_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "device_quality_compatibility"), None)
        constraints = rule.get("device_constraints", []) if rule else []

        all_text = " ".join(f.text.lower() for f in frags)
        banned_tags_all: List[str] = []
        for c in constraints:
            devices = c.get("devices", [])
            banned_tags = c.get("banned_tags", [])
            if any(re.search(rf"\b{re.escape(d)}\b", all_text, flags=re.IGNORECASE) for d in devices):
                banned_tags_all.extend(banned_tags)

        if banned_tags_all:
            frags = [
                f for f in frags
                if not any(re.search(rf"\b{re.escape(bt)}\b", f.text, flags=re.IGNORECASE) for bt in banned_tags_all)
            ]

        return frags

    # ─── 规则 8: 纹身真皮层融合 ───

    def _resolve_tattoo_fusion_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        """
        严格作用于 source_slot == 'tattoo' 或明确属于纹身 ID 的条目。
        绝对禁止使用模糊子串 'ink' 搜索整段 Prompt，避免 pink/drink/link 产生误判。
        """
        has_explicit_tattoo = any(
            f.source_slot == "tattoo" and f.text.strip()
            for f in frags
        )

        if has_explicit_tattoo:
            fusion_words = [
                "realistic tattoo",
                "ink embedded in dermis",
                "tattoo beneath skin surface",
                "follows body contours",
                "slightly faded edges",
                "pores visible through ink",
            ]
            all_text = " ".join(f.text.lower() for f in frags)
            max_order = max((f.order for f in frags), default=0)

            for fw in fusion_words:
                if fw.lower() not in all_text:
                    max_order += 1
                    frags.append(
                        PromptFragment(
                            text=fw,
                            source_slot="tattoo",
                            order=max_order,
                        )
                    )

        return frags


def sanitize_prompt(prompt: str) -> str:
    """清理最终 prompt 中的格式问题。"""
    prompt = re.sub(r"\bspinning room\b", "drunken stupor", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r",\s*,+", ",", prompt)
    prompt = prompt.strip(", \n\t")
    prompt = re.sub(r"\s{2,}", " ", prompt)
    prompt = re.sub(r",(\S)", r", \1", prompt)
    return prompt
