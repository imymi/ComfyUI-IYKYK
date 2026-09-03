"""
conflict_resolver.py — 结构化提示词冲突检测与消解引擎 (PromptAtom 生产级贯穿与 17 规则 SSOT)

严格实现 17 大多规则物理与语义自洽冲突消解：
1. spatial_environmental_mutual_exclusion: 空间与环境自洽互斥
2. nudity_clothing_conflicts: 裸露与内衣/衣物状态互斥 (无 clothing None 伪合成词)
3. material_penetration: 材质穿透伪影消解 (官方 clothing_extension Provenance 免杀)
4. clothing_style_state_coherence: 服装款式与解构状态互斥 (连体衣禁止掀裙解扣，长裤禁止裙摆)
5. gaze_angle_geometry: 视线与镜头角度几何匹配
6. gaze_mutual_exclusion: 视线方向唯一性 (直视与移开视线互斥)
7. accessory_occlusion_gaze_coherence: 饰品遮挡与视线面部动作自洽 (蒙眼禁止对视眨眼)
8. framing_lower_body_coherence: 景别特写与下肢足部元素自洽 (面部特写剔除鞋袜，保留非下肢词)
9. liquid_restrictions: 液体微量与安全法则 (先执行 banned_combos 替换，再添加微量量词)
10. device_quality_compatibility: 设备与画质等级兼容 (手机/监控自拍过滤 8k/单反/写真)
11. tattoo_dermal_fusion: 纹身真皮层融合
12. pose_hand_occupation: 姿势手部占用与手持道具互斥
13. handheld_props_single_holder: 多手持道具唯一性消解 (保留首个手持动作)
14. emotion_gaze_affinity: 情绪表情与眼神方向一致性
15. environmental_lighting_coherence: 光照环境与黑夜白昼物理自洽
16. monochrome_film_chroma_coherence: 黑白胶片与高饱和色彩互斥
17. makeup_details_coherence: 妆容与细节自洽 (素颜无妆与糊妆浓妆互斥)
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__:
    from .atomizer import atoms_to_fragments, fragments_to_atoms
    from .errors import RuleConfigurationError
    from .models import PromptAtom, PromptFragment, SpanType, TagProvenance
    from .rule_contract import (
        STABLE_RULE_ORDER,
        AngleGazeMappingSpec,
        BannedComboSpec,
        DeviceConstraintSpec,
        LevelRuleSpec,
        PatternSpec,
        ReplacementSpec,
        RuleDocument,
        parse_rule_document,
    )
else:
    from lib.atomizer import atoms_to_fragments, fragments_to_atoms
    from lib.errors import RuleConfigurationError
    from lib.models import PromptAtom, PromptFragment, SpanType, TagProvenance
    from lib.rule_contract import (
        STABLE_RULE_ORDER,
        AngleGazeMappingSpec,
        BannedComboSpec,
        DeviceConstraintSpec,
        LevelRuleSpec,
        PatternSpec,
        ReplacementSpec,
        RuleDocument,
        parse_rule_document,
    )


def match_pattern(pattern: str, text: str, mode: str = "phrase") -> bool:
    """向后兼容接口：直接代理至 PatternSpec 模式匹配引擎。"""
    if not pattern or not text:
        return False
    ps = PatternSpec(pattern=pattern, match_mode=mode)  # type: ignore[arg-type]
    return ps.matches(text)


class RuleRegistry:
    """强类型规则注册中心：直接消费 RuleDocument 统一解析结果。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.doc: RuleDocument = self._load_rules()

    def _load_rules(self) -> RuleDocument:
        rules_file = self.data_dir / "conflict_rules.json"
        if not rules_file.exists():
            raise RuleConfigurationError(f"Conflict rules file not found: {rules_file}")

        try:
            with open(rules_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise RuleConfigurationError(f"Failed to parse conflict_rules.json: {e}") from e

        # 消费权威 SSOT 统一强类型解析器 (Fail-Closed)
        return parse_rule_document(data)

    def get_rule(self, rule_id: str) -> Any:
        item = self.doc.get_rule(rule_id)
        if item is None:
            raise RuleConfigurationError(f"Rule {rule_id!r} not found in registry")
        return item.spec


class ConflictResolver:
    """17 大冲突规则消解引擎。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.registry = RuleRegistry(data_dir)

    def resolve_atoms_with_report(
        self,
        atoms: Sequence[PromptAtom],
        rng: Optional[Random] = None
    ) -> Tuple[List[PromptAtom], Tuple[str, ...]]:
        """按 STABLE_RULE_ORDER 执行 17 条冲突消解规则并报告实际触发的规则 ID 列表。"""
        if rng is None:
            rng = Random(42)

        current_atoms = list(atoms)
        rules_applied: List[str] = []
        for rule_id in STABLE_RULE_ORDER:
            rule = self.registry.get_rule(rule_id)
            resolver_fn = getattr(self, f"_resolve_{rule_id}_atoms")
            before_texts = [a.text for a in current_atoms]
            current_atoms = resolver_fn(current_atoms, rule, rng)
            after_texts = [a.text for a in current_atoms]
            if before_texts != after_texts:
                rules_applied.append(rule_id)

        return current_atoms, tuple(rules_applied)

    def resolve_atoms(
        self,
        atoms: Sequence[PromptAtom],
        rng: Optional[Random] = None
    ) -> List[PromptAtom]:
        """按 STABLE_RULE_ORDER 执行 17 条冲突消解规则。"""
        resolved, _ = self.resolve_atoms_with_report(atoms, rng)
        return resolved

    def resolve_fragments(
        self,
        fragments: Sequence[PromptFragment | str],
        rng: Optional[Random] = None
    ) -> List[PromptFragment]:
        """兼容接口：将 PromptFragment 列表通过 atomizer 转换为 PromptAtom 执行消解后，按完整 Tag 重新聚合返回。"""
        _, atoms = fragments_to_atoms(fragments)
        resolved_atoms = self.resolve_atoms(atoms, rng)
        return atoms_to_fragments(resolved_atoms)

    def resolve(
        self,
        slots: Dict[str, Sequence[str]],
        rng: Optional[Random] = None
    ) -> Dict[str, List[str]]:
        """兼容接口：接收槽位字典并返回消解后的槽位字典。"""
        if rng is None:
            rng = Random(42)

        if not isinstance(slots, dict):
            raise TypeError(f"slots must be a dict, got {type(slots).__name__}")

        fragments: List[PromptFragment] = []
        for slot_name, tags in slots.items():
            if not isinstance(slot_name, str):
                raise TypeError(f"slot name must be str, got {type(slot_name).__name__}")
            if not isinstance(tags, (list, tuple)):
                raise TypeError(f"tags for slot {slot_name!r} must be list or tuple, got {type(tags).__name__}")
            for t in tags:
                if not isinstance(t, str):
                    raise TypeError(f"tag in slot {slot_name!r} must be str, got {type(t).__name__}")
                if not t:
                    continue
                fragments.append(
                    PromptFragment(
                        text=t,
                        source_slot=slot_name,
                        order=len(fragments),
                    )
                )

        resolved_frags = self.resolve_fragments(fragments, rng)

        new_slots: Dict[str, List[str]] = {}
        for f in resolved_frags:
            new_slots.setdefault(f.source_slot, []).append(f.text)

        return new_slots

    # ─── 规则 1: spatial_environmental_mutual_exclusion ───

    def _resolve_spatial_environmental_mutual_exclusion_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        venue_clusters: Dict[str, List[PatternSpec]] = rule["venue_clusters"]
        outdoor_exclusive: List[PatternSpec] = rule["outdoor_exclusive"]
        indoor_exclusive: List[PatternSpec] = rule["indoor_exclusive"]
        deprecated_tags: List[ReplacementSpec] = rule.get("deprecated_tags", [])

        # 1. 替换废弃或歧义词条 (仅允许修改 PLAIN 内部字节)
        result: List[PromptAtom] = []
        for a in atoms:
            if a.can_modify_internal:
                txt = a.text
                for rep in deprecated_tags:
                    if rep.banned.matches(txt):
                        txt = rep.banned.substitute(txt, rep.replacement)
                result.append(replace(a, text=txt))
            else:
                result.append(a)
        atoms = result

        # 2. 场所集群互斥
        active_venues: List[Tuple[int, str]] = []
        for vname, vtags in venue_clusters.items():
            for a in atoms:
                if a.can_detect and any(vt.matches(a.text) for vt in vtags):
                    active_venues.append((a.tag_order, vname))
                    break

        if len(set(v for _, v in active_venues)) > 1:
            active_venues.sort(key=lambda x: x[0])
            dominant_venue = active_venues[0][1]
            banned_venues = [v for _, v in active_venues if v != dominant_venue]
            banned_tags_all: List[PatternSpec] = []
            for bv in banned_venues:
                banned_tags_all.extend(venue_clusters[bv])

            atoms = [
                a for a in atoms
                if not a.can_delete_atom or not any(bt.matches(a.text) for bt in banned_tags_all)
            ]

        # 3. 室内 / 室外二元互斥
        indoor_first_order = next((a.tag_order for a in atoms if a.can_detect and any(w.matches(a.text) for w in indoor_exclusive)), None)
        outdoor_first_order = next((a.tag_order for a in atoms if a.can_detect and any(w.matches(a.text) for w in outdoor_exclusive)), None)

        if indoor_first_order is not None and outdoor_first_order is not None:
            if indoor_first_order <= outdoor_first_order:
                atoms = [a for a in atoms if not a.can_delete_atom or not any(w.matches(a.text) for w in outdoor_exclusive)]
            else:
                atoms = [a for a in atoms if not a.can_delete_atom or not any(w.matches(a.text) for w in indoor_exclusive)]

        return atoms

    # ─── 规则 2: nudity_clothing_conflicts ───

    def _resolve_nudity_clothing_conflicts_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        level_rules: Dict[str, LevelRuleSpec] = rule.get("level_rules", {})

        # 识别当前激活的裸露等级
        active_level: Optional[str] = None
        for a in atoms:
            if a.source_slot == "nudity":
                for lvl_code in ("L6", "L5", "L4", "L3", "L2", "L1"):
                    if lvl_code in (a.source_item_id or "") or PatternSpec(pattern=lvl_code, match_mode="phrase").matches(a.text):
                        active_level = lvl_code
                        break
            if active_level:
                break

        if not active_level:
            return atoms

        lvl_conf = level_rules.get(active_level)
        if lvl_conf:
            result: List[PromptAtom] = []
            for a in atoms:
                if a.source_slot != "nudity" and a.can_delete_atom:
                    if any(b.matches(a.text) for b in lvl_conf.banned_patterns):
                        continue
                result.append(a)
            atoms = result

        return atoms

    # ─── 规则 3: material_penetration ───

    def _resolve_material_penetration_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        banned_words: List[PatternSpec] = rule["banned_words"]
        replacements: List[str] = rule["replacements"]
        target_slots: Tuple[str, ...] = tuple(rule.get("target_slots", ("clothing",)))
        target_provenance_kinds: Tuple[str, ...] = tuple(
            rule.get("target_provenance_kinds", ("base_clothing", "clothing_state"))
        )

        result: List[PromptAtom] = []
        for a in atoms:
            # 1. clothing_extension 优先豁免
            if a.provenance and a.provenance.kind == "clothing_extension":
                result.append(a)
                continue

            # 2. 作用域限制：必须满足 source_slot in target_slots OR provenance.kind in target_provenance_kinds
            is_target = (a.source_slot in target_slots) or (
                a.provenance is not None and a.provenance.kind in target_provenance_kinds
            )
            if not is_target:
                result.append(a)
                continue

            if not a.can_modify_internal:
                result.append(a)
                continue

            txt = a.text
            matched = any(bw.matches(txt) for bw in banned_words)

            if matched and replacements:
                rep = rng.choice(replacements)
                # 保留原 provenance，且绝不覆盖既有 rule_id (P1-2)
                result.append(replace(a, text=rep))
            else:
                result.append(a)

        return result

    # ─── 规则 4: clothing_style_state_coherence ───

    def _resolve_clothing_style_state_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        one_piece_triggers: List[PatternSpec] = rule["one_piece_triggers"]
        one_piece_banned: List[PatternSpec] = rule["one_piece_banned_states"]
        pants_triggers: List[PatternSpec] = rule.get("pants_triggers", [])
        pants_banned: List[PatternSpec] = rule.get("pants_banned_states", [])

        has_one_piece = any(a.can_detect and any(t.matches(a.text) for t in one_piece_triggers) for a in atoms)
        has_pants = any(a.can_detect and any(t.matches(a.text) for t in pants_triggers) for a in atoms)

        result: List[PromptAtom] = []
        for a in atoms:
            if not a.can_delete_atom:
                result.append(a)
                continue

            if has_one_piece and any(b.matches(a.text) for b in one_piece_banned):
                continue

            if has_pants and any(b.matches(a.text) for b in pants_banned):
                continue

            result.append(a)

        return result

    # ─── 规则 5: gaze_angle_geometry ───

    def _resolve_gaze_angle_geometry_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        mappings: List[AngleGazeMappingSpec] = rule["mappings"]

        matched_mapping: Optional[AngleGazeMappingSpec] = None
        for m in mappings:
            for a in atoms:
                if a.can_detect and any(ang.matches(a.text) for ang in m.angles):
                    matched_mapping = m
                    break
            if matched_mapping:
                break

        if not matched_mapping:
            return atoms

        # 过滤被禁止的视角动作
        result: List[PromptAtom] = []
        for a in atoms:
            if a.can_delete_atom and any(bg.matches(a.text) for bg in matched_mapping.banned_gaze):
                continue
            result.append(a)

        return result

    # ─── 规则 6: gaze_mutual_exclusion ───

    def _resolve_gaze_mutual_exclusion_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        exclusive_pairs: List[Tuple[PatternSpec, PatternSpec]] = rule["exclusive_pairs"]

        for p1, p2 in exclusive_pairs:
            atom1 = next((a for a in atoms if a.can_detect and p1.matches(a.text)), None)
            atom2 = next((a for a in atoms if a.can_detect and p2.matches(a.text)), None)

            if atom1 and atom2:
                # 冲突发生：保留 tag_order 较小者
                loser = atom2 if atom1.tag_order <= atom2.tag_order else atom1
                if loser.can_delete_atom:
                    atoms = [a for a in atoms if a is not loser]

        return atoms

    # ─── 规则 7: accessory_occlusion_gaze_coherence ───

    def _resolve_accessory_occlusion_gaze_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        occlusion_triggers: List[PatternSpec] = rule["occlusion_triggers"]
        banned_gaze_actions: List[PatternSpec] = rule["banned_gaze_actions"]

        has_occlusion = any(a.can_detect and any(ot.matches(a.text) for ot in occlusion_triggers) for a in atoms)
        if has_occlusion:
            atoms = [
                a for a in atoms
                if not a.can_delete_atom or not any(bg.matches(a.text) for bg in banned_gaze_actions)
            ]

        return atoms

    # ─── 规则 8: framing_lower_body_coherence ───

    def _resolve_framing_lower_body_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        close_up_triggers: List[PatternSpec] = rule["close_up_triggers"]
        banned_lower_body: List[PatternSpec] = rule["banned_lower_body"]

        is_close_up = any(a.can_detect and any(cut.matches(a.text) for cut in close_up_triggers) for a in atoms)
        if is_close_up:
            atoms = [
                a for a in atoms
                if not a.can_delete_atom or not any(bl.matches(a.text) for bl in banned_lower_body)
            ]

        return atoms

    # ─── 规则 9: liquid_restrictions ───

    def _resolve_liquid_restrictions_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        liquid_words: List[PatternSpec] = rule["liquid_words"]
        modifiers: List[str] = rule["modifiers"]
        banned_combos: List[BannedComboSpec] = rule["banned_combos"]

        # 1. 替换高危组合
        replaced_atoms: List[PromptAtom] = []
        for a in atoms:
            if a.can_modify_internal:
                txt = a.text
                for bc in banned_combos:
                    for trig in bc.triggers:
                        if trig.matches(txt):
                            txt = trig.substitute(txt, bc.replace)
                replaced_atoms.append(replace(a, text=txt))
            else:
                replaced_atoms.append(a)
        atoms = replaced_atoms

        # 2. 对微量液体应用量词修饰
        result: List[PromptAtom] = []
        for a in atoms:
            if a.source_slot == "liquids" and a.can_modify_internal:
                txt = a.text
                has_mod = any(m in txt.lower() for m in modifiers)
                if not has_mod and any(lw.matches(txt) for lw in liquid_words):
                    mod = rng.choice(modifiers)
                    txt = f"{mod} {txt}"
                result.append(replace(a, text=txt))
            else:
                result.append(a)

        return result

    # ─── 规则 10: device_quality_compatibility ───

    def _resolve_device_quality_compatibility_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        device_constraints: List[DeviceConstraintSpec] = rule["device_constraints"]

        banned_tags_all: List[PatternSpec] = []
        for dc in device_constraints:
            if any(a.can_detect and any(d.matches(a.text) for d in dc.devices) for a in atoms):
                banned_tags_all.extend(dc.banned_tags)

        if banned_tags_all:
            atoms = [
                a for a in atoms
                if not a.can_delete_atom or not any(bt.matches(a.text) for bt in banned_tags_all)
            ]

        return atoms

    # ─── 规则 11: tattoo_dermal_fusion ───

    def _resolve_tattoo_dermal_fusion_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        tattoo_indicators: List[PatternSpec] = rule["tattoo_indicators"]
        fusion_tags: List[str] = rule["fusion_tags"]

        has_tattoo_slot = any(a.source_slot in ("tattoo", "tattoos") for a in atoms)
        has_tattoo_text = any(a.can_detect and any(ti.matches(a.text) for ti in tattoo_indicators) for a in atoms)

        if (has_tattoo_slot or has_tattoo_text) and fusion_tags:
            all_text = " ".join(a.text.lower() for a in atoms if a.can_detect)
            has_fusion = any(ft.lower() in all_text for ft in fusion_tags)
            if not has_fusion:
                parent_atom = next(
                    (a for a in atoms if a.source_slot in ("tattoo", "tattoos") or (a.can_detect and any(ti.matches(a.text) for ti in tattoo_indicators))),
                    None
                )
                parent_ids = (parent_atom.atom_id,) if (parent_atom and parent_atom.atom_id) else ()
                chosen_fusion = rng.choice(fusion_tags)
                max_order = max((a.tag_order for a in atoms), default=0) + 1
                atoms.append(
                    PromptAtom(
                        text=chosen_fusion,
                        span_type=SpanType.PLAIN,
                        source_slot="tattoo",
                        tag_order=max_order,
                        span_order=0,
                        provenance=TagProvenance(
                            kind="resolver_generated",
                            rule_id="tattoo_dermal_fusion",
                            item_id=chosen_fusion,
                            parent_ids=parent_ids,
                        ),
                        atom_id=f"atom_resolver_tattoo_dermal_fusion_{max_order}_0",
                    )
                )

        return atoms

    # ─── 规则 12: pose_hand_occupation ───

    def _resolve_pose_hand_occupation_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        busy_triggers: List[PatternSpec] = rule["busy_pose_triggers"]
        banned_handheld: List[PatternSpec] = rule["banned_handheld_patterns"]

        is_busy = any(a.can_detect and any(bt.matches(a.text) for bt in busy_triggers) for a in atoms)
        if is_busy:
            atoms = [
                a for a in atoms
                if not a.can_delete_atom or not any(bh.matches(a.text) for bh in banned_handheld)
            ]

        return atoms

    # ─── 规则 13: handheld_props_single_holder ───

    def _resolve_handheld_props_single_holder_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        handheld_patterns: List[PatternSpec] = rule["handheld_patterns"]

        handheld_atoms = [
            a for a in atoms
            if a.can_detect and any(hp.matches(a.text) for hp in handheld_patterns)
        ]

        if len(handheld_atoms) > 1:
            handheld_atoms.sort(key=lambda x: (x.tag_order, x.span_order))
            to_remove = set(handheld_atoms[1:])
            atoms = [a for a in atoms if a not in to_remove or not a.can_delete_atom]

        return atoms

    # ─── 规则 14: emotion_gaze_affinity ───

    def _resolve_emotion_gaze_affinity_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        conflicts = rule["conflicts"]

        for c in conflicts:
            emotion_triggers: List[PatternSpec] = c["emotion_triggers"]
            banned_gaze: List[PatternSpec] = c["banned_gaze"]

            has_emotion = any(a.can_detect and any(et.matches(a.text) for et in emotion_triggers) for a in atoms)
            if has_emotion:
                atoms = [
                    a for a in atoms
                    if not a.can_delete_atom or not any(bg.matches(a.text) for bg in banned_gaze)
                ]

        return atoms

    # ─── 规则 15: environmental_lighting_coherence ───

    def _resolve_environmental_lighting_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        daylight_triggers: List[PatternSpec] = rule["daylight_triggers"]
        banned_night: List[PatternSpec] = rule["banned_night_elements"]

        has_night_scene = any(
            a.can_detect and a.source_slot in ("scene_theme", "scene", "theme") and
            any(bn.matches(a.text) for bn in banned_night)
            for a in atoms
        )
        has_daylight_lighting = any(
            a.can_detect and a.source_slot in ("lighting", "lighting_palette") and
            any(dt.matches(a.text) for dt in daylight_triggers)
            for a in atoms
        )

        # 优先级：场景主锚点 (scene_theme) > 环境光影 (lighting)
        if has_night_scene and has_daylight_lighting:
            # 夜景为主场所：保留夜景场景，剔除冲突的日间光照
            return [
                a for a in atoms
                if not (a.can_delete_atom and a.source_slot in ("lighting", "lighting_palette") and any(dt.matches(a.text) for dt in daylight_triggers))
            ]

        has_daylight = any(a.can_detect and any(dt.matches(a.text) for dt in daylight_triggers) for a in atoms)
        if has_daylight:
            atoms = [
                a for a in atoms
                if not (a.can_delete_atom and any(bn.matches(a.text) for bn in banned_night))
            ]

        return atoms

    # ─── 规则 16: monochrome_film_chroma_coherence ───

    def _resolve_monochrome_film_chroma_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        monochrome_triggers: List[PatternSpec] = rule["monochrome_triggers"]
        banned_chroma: List[PatternSpec] = rule["banned_chroma"]

        has_mono = any(a.can_detect and any(mt.matches(a.text) for mt in monochrome_triggers) for a in atoms)
        if has_mono:
            atoms = [
                a for a in atoms
                if not a.can_delete_atom or not any(bc.matches(a.text) for bc in banned_chroma)
            ]

        return atoms

    # ─── 规则 17: makeup_details_coherence ───

    def _resolve_makeup_details_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule: Dict[str, Any],
        rng: Random
    ) -> List[PromptAtom]:
        no_makeup_triggers: List[PatternSpec] = rule["no_makeup_triggers"]
        banned_makeup_smudge: List[PatternSpec] = rule["banned_makeup_smudge"]

        has_no_makeup = any(a.can_detect and any(nmt.matches(a.text) for nmt in no_makeup_triggers) for a in atoms)
        if has_no_makeup:
            atoms = [
                a for a in atoms
                if not a.can_delete_atom or not any(bms.matches(a.text) for bms in banned_makeup_smudge)
            ]

        return atoms
