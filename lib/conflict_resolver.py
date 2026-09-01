"""
conflict_resolver.py — 结构化提示词冲突检测与消解引擎

严格实现 12 大多规则物理与语义自洽冲突消解：
1. 空间与环境自洽互斥（按片段顺序锁定主场所，禁止多个独立场所/室内外冲突并存）
2. 裸露与内衣/衣物状态互斥（私处暴露自动剔除内裤，全裸自动剔除穿着描述）
3. 材质穿透伪影消解（剔除 sheer/see-through 等崩图词，替换为真实物理脱法）
4. 视线与镜头角度几何匹配（仰拍强制俯视、俯拍强制仰视、POV 强制直视）
5. 视线方向唯一性（消解直视与移开视线互斥）
6. 液体微量法则与安全渲染（自动添加微量量词，拦截闭眼精液白内障）
7. 设备与画质等级兼容（监控/手机自拍自动过滤 8k/单反/写真标签）
8. 纹身真皮层融合（仅针对纹身槽位或显式纹身词条，杜绝 pink/drink/link 误触发）
9. 姿势手部占用与手持道具互斥（双手占用时自动剔除手持动作，根除多手伪影）
10. 情绪表情与眼神方向一致性（害羞/冷淡与直视/眨眼人设亲和消解）
11. 光照环境与场景黑夜/白昼物理自洽（日间自然光与深夜场所/天气互斥）
12. 妆容与细节自洽（素颜无妆与糊妆/浓妆互斥）
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import PromptFragment


def is_protected_fragment(text: str) -> bool:
    """判断片段是否为受保护的结构化语法（如 LoRA、双引号、转义字符、权重括号等）。"""
    s = text.strip()
    if not s:
        return False
    if s.startswith("<") and s.endswith(">"):
        return True
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return True
    if "\\" in s:
        return True
    if (s.startswith("(") and s.endswith(")")) or (s.startswith("[") and s.endswith("]")):
        return True
    return False


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
        核心方法：对结构化 PromptFragment 列表执行 12 大物理与语义自洽冲突消解。
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

        # 4. 服装款式与解构状态互斥 (Rule 16)
        frags = self._resolve_clothing_style_state_fragments(frags, rules, rng)

        # 5. 视线与镜头角度几何匹配
        frags = self._resolve_gaze_angle_fragments(frags, rules, rng)

        # 6. 视线方向互斥消解
        frags = self._resolve_gaze_mutual_exclusion_fragments(frags, rules, rng)

        # 7. 饰品遮挡与视线/面部动作自洽 (Rule 14)
        frags = self._resolve_accessory_occlusion_gaze_fragments(frags, rules, rng)

        # 8. 景别特写与下肢/足部元素自洽 (Rule 13)
        frags = self._resolve_framing_lower_body_fragments(frags, rules, rng)

        # 9. 液体微量与安全法则
        frags = self._resolve_liquids_fragments(frags, rules, rng)

        # 10. 设备与画质兼容
        frags = self._resolve_device_quality_fragments(frags, rules, rng)

        # 11. 纹身真皮层融合（严格基于纹身槽位或显式纹身标记）
        frags = self._resolve_tattoo_fusion_fragments(frags, rules, rng)

        # 12. 姿势手部占用与手持道具互斥（杜绝三只手/动作矛盾伪影）
        frags = self._resolve_pose_hand_fragments(frags, rules, rng)

        # 13. 多手持道具唯一性消解 (Rule 17)
        frags = self._resolve_handheld_props_single_holder_fragments(frags, rules, rng)

        # 14. 情绪表情与眼神方向一致性（杜绝害羞对视/冷淡眨眼人设割裂）
        frags = self._resolve_emotion_gaze_affinity_fragments(frags, rules, rng)

        # 15. 光照环境与场景黑夜/白昼物理自洽
        frags = self._resolve_environmental_lighting_fragments(frags, rules, rng)

        # 16. 黑白胶片与高饱和色彩互斥 (Rule 15)
        frags = self._resolve_monochrome_chroma_fragments(frags, rules, rng)

        # 17. 妆容与细节自洽（素颜与糊妆互斥）
        frags = self._resolve_makeup_details_fragments(frags, rules, rng)

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

        # 1. 替换废弃或歧义词条 (受保护结构语法如 LoRA/引号/转义等绝对保持原样)
        cleaned_frags: List[PromptFragment] = []
        for f in frags:
            txt = f.text
            if not is_protected_fragment(txt):
                for old_w, new_w in deprecated_map.items():
                    if re.search(rf"\b{re.escape(old_w)}\b", txt, flags=re.IGNORECASE):
                        txt = re.sub(rf"\b{re.escape(old_w)}\b", new_w, txt, flags=re.IGNORECASE)
            cleaned_frags.append(replace(f, text=txt))
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

        # 1. 识别当前激活的裸露等级 (L1 - L6)
        active_level: Optional[str] = None
        for f in frags:
            if f.source_slot == "nudity" and f.source_item_id in ("L1", "L2", "L3", "L4", "L5", "L6"):
                active_level = f.source_item_id
                break

        if not active_level:
            for f in frags:
                if f.source_slot == "nudity":
                    txt_low = f.text.lower()
                    if any(k in txt_low for k in ["fully clothed", "dressed", "clothed body", "modest outfit", "nothing exposed", "everything covered", "form-fitting"]):
                        active_level = "L1"
                        break
                    elif any(k in txt_low for k in ["cleavage visible", "collarbone exposed", "skirt slit revealing", "one button undone", "shoulder slipping out"]):
                        active_level = "L2"
                        break
                    elif any(k in txt_low for k in ["topless with skirt", "bra removed", "shirt open showing bare breasts", "dress pulled down to waist", "breasts bare waist covered"]):
                        active_level = "L3"
                        break
                    elif any(k in txt_low for k in ["only panties", "just stockings", "garter belt only", "nothing but thigh-highs", "only underwear"]):
                        active_level = "L4"
                        break
                    elif any(k in txt_low for k in ["completely naked", "fully nude", "full frontal nudity", "all clothes removed", "stripped bare", "no clothing at all"]):
                        active_level = "L5"
                        break
                    elif any(k in txt_low for k in ["legs spread wide", "pussy fully displayed", "spreading labia", "vaginal opening", "erotic close-up"]):
                        active_level = "L6"
                        break

        # 2. 如果存在 active_level，根据 level_rules 执行多层强力过滤
        level_rules = rule.get("level_rules", {}) if rule else {}
        banned_patterns = level_rules.get(active_level, {}).get("banned_patterns", []) if active_level else []

        if banned_patterns:
            cleaned_frags: List[PromptFragment] = []
            for f in frags:
                # 裸露槽位本身的定义词保留
                if f.source_slot == "nudity":
                    cleaned_frags.append(f)
                    continue

                txt_low = f.text.lower()
                # 检查该片段是否包含该等级下的禁用模式
                if any(bp.lower() in txt_low or re.search(rf"\b{re.escape(bp.lower())}\b", txt_low) for bp in banned_patterns):
                    continue

                cleaned_frags.append(f)
            frags = cleaned_frags

        # 3. 经典局部互斥规则（如 pussy visible vs panties showing, topless vs wearing bra 等）
        conflicts = rule.get("conflicts", []) if rule else [
            {"trigger": ["pussy visible", "exposed vagina", "spread pussy", "bare pussy"], "ban": ["panties showing", "wearing panties", "wearing underwear"]},
            {"trigger": ["topless", "bare breasts"], "ban": ["wearing bra", "bra on"]},
            {"trigger": ["completely naked", "full nude"], "ban": ["wearing blazer", "wearing skirt", "wearing dress"]},
            {"trigger": ["no panties", "pussy visible"], "ban": ["cameltoe"]},
        ]

        banned_set: set[str] = set()
        current_text = " ".join(f.text.lower() for f in frags)
        for item in conflicts:
            triggers = item.get("trigger", [])
            bans = item.get("ban", [])
            if any(tr.lower() in current_text for tr in triggers):
                for b in bans:
                    banned_set.add(b.lower())

        if banned_set:
            frags = [
                f for f in frags
                if not any(b in f.text.lower() for b in banned_set)
            ]

        # 4. 防御性补底：如果 L1 下衣服被全部清空，补充整齐穿着词
        if active_level == "L1":
            has_clothing = any(f.source_slot == "clothing" for f in frags)
            if not has_clothing:
                frags.append(
                    PromptFragment(
                        text="neatly dressed, form-fitting clothing",
                        source_slot="clothing",
                        source_item_id="L1",
                        order=35,
                    )
                )

        return frags

    # ─── 规则 3: 材质穿透伪影消解 ───

    def _resolve_material_penetration_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "material_penetration"), None)
        banned_words = rule.get("banned_words", ["sheer", "see-through", "transparent fabric", "see through", "sheer fabric", "translucent dress"]) if rule else ["sheer", "see-through", "transparent"]

        # 探测当前是否为 L1 纯包裹
        is_l1 = any(f.source_slot == "nudity" and (f.source_item_id == "L1" or "fully clothed" in f.text.lower() or "nothing exposed" in f.text.lower()) for f in frags)

        cleaned: List[PromptFragment] = []
        had_banned = False
        for f in frags:
            txt = f.text
            # 官方透度/情趣扩展标签与受保护语法在非 L1 下予以合法保留，不盲目剔除
            if is_protected_fragment(txt) or (not is_l1 and any(et in txt.lower() for et in [
                "sheer chiffon", "sheer mesh", "semi-translucent", "backlit translucent", "highly translucent", "ultra-thin gossamer"
            ])):
                cleaned.append(f)
                continue

            for bw in banned_words:
                if re.search(rf"\b{re.escape(bw)}\b", txt, flags=re.IGNORECASE):
                    txt = re.sub(rf"\b{re.escape(bw)}\b", "", txt, flags=re.IGNORECASE).strip()
                    had_banned = True
            if txt:
                cleaned.append(replace(f, text=txt))

        if had_banned:
            if is_l1:
                rep = "form-fitting fabric clinging to silhouette"
            else:
                replacements = rule.get("replacements", ["unbuttoned", "lifted up", "slipping off shoulder", "wet dress clinging tightly to skin"]) if rule else ["unbuttoned", "lifted up"]
                rep = rng.choice(replacements) if replacements else "wet dress clinging tightly to skin"

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

    # ─── 规则 9: 姿势手部占用与手持道具互斥 ───

    def _resolve_pose_hand_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "pose_hand_occupation"), None)
        busy_triggers = rule.get("busy_pose_triggers", []) if rule else [
            "hands behind back", "hands tied", "hands bound", "arms above head",
            "arms raised high above head", "hands on head", "hands behind head",
            "gripping sheets", "gripping bedsheet", "clutching pillow with both hands",
            "pulling shirt over head", "unhooking bra behind back",
            "pulling panties down with both hands", "on all fours", "on hands and knees",
            "hands on floor", "crawling on floor", "spreading labia with both hands",
            "holding legs open", "arms wrapped around neck", "hands braced on chest",
            "covering eyes with both hands", "hands clasped behind back",
            "hands over mouth to silence", "hands clasped in prayer"
        ]
        banned_handheld = rule.get("banned_handheld_patterns", []) if rule else [
            "smartphone in hand recording", "holding smartphone", "phone held in hand",
            "holding game controller", "holding black compact digital camera", "holding camera",
            "holding embroidered round silk fan", "holding folding fan", "holding round fan",
            "holding fan in hand", "holding oiled paper umbrella propped on shoulder",
            "holding oil paper umbrella", "holding umbrella in hand",
            "holding lush fresh floral bouquet", "holding flower bouquet",
            "hugging large fluffy plush teddy bear", "holding wine glass in hand",
            "holding wine glass", "holding champagne glass", "wine glass in hand",
            "swirling glass in hand", "holding wand vibrator", "holding controller",
            "holding tea cup", "holding cigarette", "cigarette between fingers",
            "holding sword", "holding microphone", "holding tray"
        ]

        all_text = " ".join(f.text.lower() for f in frags)
        is_busy_pose = any(re.search(rf"\b{re.escape(bt.lower())}\b", all_text) or bt.lower() in all_text for bt in busy_triggers)

        if is_busy_pose and banned_handheld:
            cleaned: List[PromptFragment] = []
            for f in frags:
                txt_low = f.text.lower()
                if any(re.search(rf"\b{re.escape(bp.lower())}\b", txt_low) or bp.lower() in txt_low for bp in banned_handheld):
                    continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 10: 情绪表情与眼神方向一致性 ───

    def _resolve_emotion_gaze_affinity_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "emotion_gaze_affinity"), None)
        conflicts = rule.get("conflicts", []) if rule else []

        all_text = " ".join(f.text.lower() for f in frags)
        banned_gaze_set: set[str] = set()

        for c in conflicts:
            em_triggers = c.get("emotion_triggers", [])
            banned_g = c.get("banned_gaze", [])
            if any(et.lower() in all_text for et in em_triggers):
                for bg in banned_g:
                    banned_gaze_set.add(bg.lower())

        if banned_gaze_set:
            cleaned: List[PromptFragment] = []
            for f in frags:
                txt_low = f.text.lower()
                if any(re.search(rf"\b{re.escape(bg)}\b", txt_low) or bg in txt_low for bg in banned_gaze_set):
                    continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 11: 光照环境与黑夜场景/天气物理自洽 ───

    # ─── 规则 11: 光照环境与黑夜场景/天气物理自洽（场景主锚点优先） ───

    def _resolve_environmental_lighting_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "environmental_lighting_coherence"), None)
        daylight_triggers = rule.get("daylight_triggers", []) if rule else [
            "natural daylight", "bright daylight", "sunlight through window", "morning sunlight",
            "golden hour sunset sidelight", "golden hour afternoon light", "soft sunlight filtered through sheer curtains",
            "dappled sunlight filtering through tree canopy", "bright natural lighting", "sunlit room",
            "daytime outdoor lighting", "clear blue sky"
        ]
        banned_night = rule.get("banned_night_elements", []) if rule else [
            "hotel balcony night", "park at night", "beach at night", "forest clearing night",
            "dark alleyway at night", "night club neon", "hotel corridor late night",
            "midnight convenience store", "deep dark night", "pitch black background",
            "neon glowing nightclub", "night skyline outside window"
        ]

        has_night_scene = any(
            f.source_slot in ("scene_theme", "scene", "theme") and
            any(re.search(rf"\b{re.escape(bn.lower())}\b", f.text.lower()) or bn.lower() in f.text.lower() for bn in banned_night)
            for f in frags
        )
        has_daylight_lighting = any(
            f.source_slot in ("lighting", "lighting_palette") and
            any(re.search(rf"\b{re.escape(dt.lower())}\b", f.text.lower()) or dt.lower() in f.text.lower() for dt in daylight_triggers)
            for f in frags
        )

        # 优先级：场景主锚点 (scene_theme) > 环境光影 (lighting)
        if has_night_scene and has_daylight_lighting:
            # 夜景为主场所：保留夜景场景，剔除冲突的日间光照
            cleaned: List[PromptFragment] = []
            for f in frags:
                if f.source_slot in ("lighting", "lighting_palette"):
                    txt_low = f.text.lower()
                    if any(re.search(rf"\b{re.escape(dt.lower())}\b", txt_low) or dt.lower() in txt_low for dt in daylight_triggers):
                        continue
                cleaned.append(f)
            return cleaned

        all_text = " ".join(f.text.lower() for f in frags)
        is_daylight = any(re.search(rf"\b{re.escape(dt.lower())}\b", all_text) or dt.lower() in all_text for dt in daylight_triggers)

        if is_daylight and banned_night:
            # 日间为主：仅剔除偶发冲突的非场景主锚点夜景词条
            cleaned = []
            for f in frags:
                if f.source_slot not in ("scene_theme", "scene"):
                    txt_low = f.text.lower()
                    if any(re.search(rf"\b{re.escape(bn.lower())}\b", txt_low) or bn.lower() in txt_low for bn in banned_night):
                        continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 12: 妆容与细节自洽（素颜与糊妆互斥） ───

    def _resolve_makeup_details_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "makeup_details_coherence"), None)
        no_makeup_triggers = rule.get("no_makeup_triggers", []) if rule else [
            "bare skin no makeup", "natural bare face", "clean bare skin", "no makeup look", "makeup-free"
        ]
        banned_smudge = rule.get("banned_makeup_smudge", []) if rule else [
            "mascara running", "smudged eyeliner", "lipstick smeared", "messy running makeup", "smeared lip gloss"
        ]

        all_text = " ".join(f.text.lower() for f in frags)
        is_no_makeup = any(nmt.lower() in all_text for nmt in no_makeup_triggers)

        if is_no_makeup and banned_smudge:
            cleaned: List[PromptFragment] = []
            for f in frags:
                txt_low = f.text.lower()
                if any(re.search(rf"\b{re.escape(bs.lower())}\b", txt_low) or bs.lower() in txt_low for bs in banned_smudge):
                    continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 13: 景别特写与下肢/足部元素自洽 ───

    def _resolve_framing_lower_body_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "framing_lower_body_coherence"), None)
        close_up_triggers = rule.get("close_up_triggers", []) if rule else [
            "extreme close-up", "macro detail shot", "close-up", "face shot filling frame", "focused on facial expression",
            "portrait close-up", "tight headshot", "macro shot of lips", "eyes close-up", "facial macro shot"
        ]
        banned_lower_body = rule.get("banned_lower_body", []) if rule else [
            "garter straps", "garter belt", "garter_stockings", "bare feet", "high heels",
            "thigh-high stockings", "stiletto heels", "knee-high boots", "strappy sandals", "feet visible", "kneeling on tatami"
        ]

        has_close_up = any(
            f.source_slot in ("shot_type", "shot", "composition") and
            any(re.search(rf"\b{re.escape(ct.lower())}\b", f.text.lower()) or ct.lower() in f.text.lower() for ct in close_up_triggers)
            for f in frags
        )
        if not has_close_up:
            all_text = " ".join(f.text.lower() for f in frags)
            has_close_up = any(re.search(rf"\b{re.escape(ct.lower())}\b", all_text) for ct in close_up_triggers)

        if has_close_up and banned_lower_body:
            cleaned: List[PromptFragment] = []
            for f in frags:
                if f.source_slot in ("shot_type", "shot", "composition"):
                    cleaned.append(f)
                    continue
                txt_low = f.text.lower()
                if any(re.search(rf"\b{re.escape(bl.lower())}\b", txt_low) or bl.lower() in txt_low for bl in banned_lower_body):
                    continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 14: 饰品遮挡与视线/面部动作自洽 ───

    def _resolve_accessory_occlusion_gaze_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "accessory_occlusion_gaze_coherence"), None)
        occlusion_triggers = rule.get("occlusion_triggers", []) if rule else [
            "black lace blindfold covering eyes", "sheer patterned eye mask", "eyes closed in ecstasy", "sleeping with eyes closed",
            "blindfold", "eyes blindfolded", "silk blindfold", "covering eyes with hands", "covering eyes with both hands", "hands over eyes"
        ]
        banned_gaze = rule.get("banned_gaze_actions", []) if rule else [
            "direct eye contact", "looking at viewer", "looking up at camera", "looking down at camera", "winking", "playful wink",
            "direct eye contact with camera", "staring into lens", "dilated pupils", "sparkling eyes", "intense eye contact"
        ]

        all_text = " ".join(f.text.lower() for f in frags)
        has_occlusion = any(re.search(rf"\b{re.escape(ot.lower())}\b", all_text) or ot.lower() in all_text for ot in occlusion_triggers)

        if has_occlusion and banned_gaze:
            cleaned: List[PromptFragment] = []
            for f in frags:
                txt_low = f.text.lower()
                if any(re.search(rf"\b{re.escape(bg.lower())}\b", txt_low) or bg.lower() in txt_low for bg in banned_gaze):
                    continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 15: 黑白胶片与高饱和色彩互斥 ───

    def _resolve_monochrome_chroma_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "monochrome_film_chroma_coherence"), None)
        monochrome_triggers = rule.get("monochrome_triggers", []) if rule else [
            "high contrast b&w", "fine grain b&w", "classic monochrome", "professional monochrome", "rich tonal b&w", "warm brown monochrome",
            "kodak tri-x 400", "ilford hp5 plus", "fujifilm acros 100", "black and white film", "monochrome photography", "b&w film stock"
        ]
        banned_chroma = rule.get("banned_chroma", []) if rule else [
            "vibrant neon glow", "neon rim lighting", "cyan and magenta lighting", "rainbow prism flares", "colorful reflections", "sunset orange warmth",
            "vibrant neon cyan and magenta", "cyberpunk neon glow", "bright saturated colors", "pastel candy palette", "electric purple rim light"
        ]

        all_text = " ".join(f.text.lower() for f in frags)
        is_monochrome = any(re.search(rf"\b{re.escape(mt.lower())}\b", all_text) or mt.lower() in all_text for mt in monochrome_triggers)

        if is_monochrome and banned_chroma:
            cleaned: List[PromptFragment] = []
            for f in frags:
                txt_low = f.text.lower()
                if any(re.search(rf"\b{re.escape(bc.lower())}\b", txt_low) or bc.lower() in txt_low for bc in banned_chroma):
                    continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 16: 服装款式与解构状态互斥 ───

    def _resolve_clothing_style_state_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "clothing_style_state_coherence"), None)
        one_piece_triggers = rule.get("one_piece_triggers", []) if rule else [
            "one-piece swimsuit", "school swimsuit (sukumizu)", "sukumizu", "competition swimsuit", "leotard", "bodysuit", "bodystocking"
        ]
        one_piece_banned = rule.get("one_piece_banned_states", []) if rule else [
            "unbuttoned dress shirt", "unbuttoned blouse", "unbuttoning shirt", "skirt lifted", "skirt hiked up", "skirt slit revealing", "lifting pleated skirt", "unzipped jeans", "unzipping pants", "button undone"
        ]
        pants_triggers = rule.get("pants_triggers", []) if rule else [
            "skinny jeans", "denim jeans", "leather pants", "cargo pants", "tailored trousers", "denim shorts", "hot pants"
        ]
        pants_banned = rule.get("pants_banned_states", []) if rule else [
            "skirt slit revealing", "lifting skirt", "skirt hiked up to waist", "pleated skirt floating", "skirt blown by wind"
        ]

        all_text = " ".join(f.text.lower() for f in frags)
        is_one_piece = any(re.search(rf"\b{re.escape(op.lower())}\b", all_text) or op.lower() in all_text for op in one_piece_triggers)
        is_pants = any(re.search(rf"\b{re.escape(pt.lower())}\b", all_text) or pt.lower() in all_text for pt in pants_triggers)

        banned_states: set[str] = set()
        if is_one_piece:
            banned_states.update(s.lower() for s in one_piece_banned)
        if is_pants:
            banned_states.update(s.lower() for s in pants_banned)

        if banned_states:
            cleaned: List[PromptFragment] = []
            for f in frags:
                txt_low = f.text.lower()
                if any(re.search(rf"\b{re.escape(bs)}\b", txt_low) or bs in txt_low for bs in banned_states):
                    continue
                cleaned.append(f)
            return cleaned

        return frags

    # ─── 规则 17: 多手持道具唯一性消解 ───

    def _resolve_handheld_props_single_holder_fragments(
        self,
        frags: List[PromptFragment],
        rules: List[Dict],
        rng: Random
    ) -> List[PromptFragment]:
        rule = next((r for r in rules if r.get("id") == "handheld_props_single_holder"), None)
        handheld_patterns = rule.get("handheld_patterns", []) if rule else [
            "holding smartphone", "holding camera", "holding folding fan", "holding round silk fan", "holding round fan",
            "holding umbrella", "holding bouquet", "holding wine glass", "holding champagne glass", "holding tea cup",
            "holding sword", "holding wand vibrator", "holding microphone", "holding tray", "holding game controller",
            "holding black compact digital camera", "holding oiled paper umbrella"
        ]

        held_indices: List[int] = []
        for idx, f in enumerate(frags):
            txt_low = f.text.lower()
            if any(re.search(rf"\b{re.escape(hp.lower())}\b", txt_low) or hp.lower() in txt_low for hp in handheld_patterns):
                held_indices.append(idx)

        if len(held_indices) > 1:
            drop_indices = set(held_indices[1:])
            return [f for i, f in enumerate(frags) if i not in drop_indices]

        return frags


def sanitize_prompt(prompt: str) -> str:
    """清理最终 prompt 中的边界多余符号，严禁破坏结构语法（如 LoRA 内部空格、逗号、转义字符、括号等）。"""
    return prompt.strip(", \n\t")
