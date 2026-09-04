"""
test_pipeline_bypass_and_provenance.py — 四大入口无旁路穿透、流水线装配截断与全链溯源测试
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from random import Random
from typing import Set

from lib.assembler import assemble_result
from lib.conflict_resolver import ConflictResolver
from lib.errors import PromptValidationError
from lib.models import GenerationResult, PromptFragment, SpanType, TagProvenance
import nodes

DATA_DIR = Path(__file__).parent.parent / "data"


class TestPipelineBypassAndProvenance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = nodes.IYKYKPromptGenerator()
        cls.resolver = ConflictResolver(DATA_DIR)

        # 收集所有正规目录的选择器与项目 ID 白名单
        cls.all_catalog_ids: Set[str] = set()
        for f in DATA_DIR.glob("*.json"):
            if f.name in ("conflict_rules.json", "context_affinity.json"):
                continue
            doc = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                for k, v in doc.items():
                    if isinstance(v, list):
                        for it in v:
                            if isinstance(it, dict) and "id" in it:
                                cls.all_catalog_ids.add(it["id"])
                                if "items" in it:
                                    for sub in it["items"]:
                                        if isinstance(sub, dict) and "id" in sub:
                                            cls.all_catalog_ids.add(sub["id"])
                                if "sub_categories" in it:
                                    for sub in it["sub_categories"]:
                                        if isinstance(sub, dict) and "id" in sub:
                                            cls.all_catalog_ids.add(sub["id"])

    # ───────────────────────────────────────────────────────────
    # 1. 四大入口无旁路穿透验证
    # ───────────────────────────────────────────────────────────

    def test_01_four_entry_points_no_bypass(self):
        """四大入口验证：所有入口均通过统一的冲突消解与决策账本流水线，无旁路穿透。"""
        kwargs = {
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "和室",
            "剧情主题": "紧缚/和式SM系列",
            "景别构图": "自动 (Auto)",
            "拍摄视角": "自动 (Auto)",
            "裸露等级": "L5 极致全裸 (Full Nude)",
            "服装款式": "和服 (Kimono)",
            "服装状态": "整齐穿着 (Normal Wearing)",
            "发型发色": "随机 (Random)",
            "饰品头饰": "无 (None)",
            "妆容细节": "无 (None)",
            "姿势动作": "随机 (Random)",
            "情绪表情": "随机 (Random)",
            "光影预设": "自动 (Auto)",
            "胶片风格": "无 (None)",
            "液体效果": "无 (None)",
            "纹身标记": "💕 可爱小爱心/手腕微图案",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "高清写真 (High)",
        }
        seed = 42

        # 入口 1: generate_structured
        res_struct = self.generator.generate_structured(**kwargs, prompt_seed=seed)
        self.assertIsInstance(res_struct, GenerationResult)
        self.assertIsNotNone(res_struct.resolution_report)
        self.assertGreater(len(res_struct.resolution_report.decisions), 0)
        self.assertIn("tattoo_dermal_fusion", res_struct.rules_applied)

        # 入口 2: generate (与 generate_structured 产出 100% 字节一致)
        pos, neg, desc = self.generator.generate(**kwargs, prompt_seed=seed)
        self.assertEqual(pos, res_struct.positive)
        self.assertEqual(neg, res_struct.negative)
        self.assertEqual(desc, res_struct.description)

        # 入口 3: 纯函数 _generate_structured
        res_pure = nodes._generate_structured(
            sampler=nodes._sampler,
            assembler=nodes._assembler,
            inputs=kwargs,
            rng=Random(seed),
            effective_seed=seed,
        )
        self.assertEqual(res_pure.positive, res_struct.positive)
        self.assertEqual(res_pure.resolution_report.output_atom_hash, res_struct.resolution_report.output_atom_hash)

        # 入口 4: 直接消解器调用
        res_atoms, applied, report = self.resolver.resolve_atoms_with_full_report(
            res_struct.source_atoms,
            Random(seed),
            res_struct.context_profile,
        )
        self.assertEqual(report.input_atom_hash, res_struct.resolution_report.input_atom_hash)
        self.assertEqual(report.output_atom_hash, res_struct.resolution_report.output_atom_hash)

    # ───────────────────────────────────────────────────────────
    # 2. 装配与截断全程账本保全
    # ───────────────────────────────────────────────────────────

    def test_02_assembly_and_truncation_preserves_ledger(self):
        """装配与词数截断时，审计账本与 source_atoms 全程无损保全。"""
        frags = [
            PromptFragment(
                text="completely naked",
                source_slot="nudity",
                source_item_id="nude_l5",
                order=0,
                provenance=TagProvenance(item_id="nude_l5", parent_ids=("nude_l5",)),
            ),
            PromptFragment(
                text="wearing uniform",
                source_slot="clothing",
                source_item_id="school_uniform",
                order=1,
                provenance=TagProvenance(item_id="school_uniform", parent_ids=("school_uniform",)),
            ),
            PromptFragment(
                text="soft lighting",
                source_slot="lighting",
                source_item_id="soft_light",
                order=2,
                provenance=TagProvenance(item_id="soft_light", parent_ids=("soft_light",)),
            ),
        ]

        # 1. 充足词数预算装配
        res_full = assemble_result(frags, DATA_DIR, max_words=250)
        self.assertIsNotNone(res_full.resolution_report)
        self.assertGreater(len(res_full.resolution_report.decisions), 0)
        self.assertEqual(len(res_full.source_atoms), len(frags))

        # 2. 严苛词数预算装配 (导致后续 tag 被截断)
        res_truncated = assemble_result(frags, DATA_DIR, max_words=3)
        # 即使被截断，消解决策账本与 source_atoms 依然 100% 完整保留
        self.assertIsNotNone(res_truncated.resolution_report)
        self.assertEqual(
            res_truncated.resolution_report.output_atom_hash,
            res_full.resolution_report.output_atom_hash,
        )
        self.assertEqual(len(res_truncated.source_atoms), len(frags))
        self.assertLess(len(res_truncated.accepted_atoms), len(res_full.accepted_atoms))

    def test_03_assembly_boundary_negatives(self):
        """装配词数边界负向测试：超长原子与非法 max_words 必须被拦截。"""
        frags = [
            PromptFragment(
                text="this is a very long atomic description exceeding five words easily",
                source_slot="props",
                source_item_id="long_prop",
                order=0,
            ),
        ]
        # 单原子超过 max_words
        with self.assertRaises(PromptValidationError):
            assemble_result(frags, DATA_DIR, max_words=5)

        # 非法 max_words
        with self.assertRaises(PromptValidationError):
            assemble_result(frags, DATA_DIR, max_words=-1)

        with self.assertRaises(PromptValidationError):
            assemble_result(frags, DATA_DIR, max_words=True)

    # ───────────────────────────────────────────────────────────
    # 3. parent_ids 1:1 闭环追溯
    # ───────────────────────────────────────────────────────────

    def test_04_parent_ids_one_to_one_trace_to_source_or_catalog(self):
        """全链追溯：accepted_atoms 的所有 parent_ids 1:1 溯源至正规目录条目或 source_atoms。"""
        kwargs = {
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "教室",
            "剧情主题": "教师/学生系列",
            "景别构图": "近景 CU (面部表情/眼神)",
            "拍摄视角": "自动 (Auto)",
            "裸露等级": "L1 包裹暗示 (Fully Clothed / Suggestive)",
            "服装款式": "水手服 (JK Sailor Suit)",
            "服装状态": "整齐穿着 (Normal Wearing)",
            "发型发色": "随机 (Random)",
            "饰品头饰": "无 (None)",
            "妆容细节": "无 (None)",
            "姿势动作": "随机 (Random)",
            "情绪表情": "随机 (Random)",
            "光影预设": "自动 (Auto)",
            "胶片风格": "无 (None)",
            "液体效果": "无 (None)",
            "纹身标记": "无 (None)",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "高清写真 (High)",
        }
        res = self.generator.generate_structured(**kwargs, prompt_seed=12345)

        source_atom_ids = {a.atom_id for a in res.source_atoms}
        source_item_ids = {a.provenance.item_id for a in res.source_atoms if a.provenance.item_id}

        self.assertGreater(len(res.atoms), 0)

        for atom in res.atoms:
            self.assertIsNotNone(atom.provenance)
            # 每个 atom 的 parent_ids 必须闭环溯源
            for pid in atom.provenance.parent_ids:
                is_valid_trace = (
                    pid in self.all_catalog_ids
                    or pid in source_atom_ids
                    or pid in source_item_ids
                    or pid.startswith("linkage_")
                    or pid.startswith("clothing:")
                    or pid.startswith("rule:")
                    or pid in ("quality_default", "atom_generator", "resolver")
                )
                self.assertTrue(
                    is_valid_trace,
                    f"Orphan parent_id '{pid}' in atom '{atom.text}' (item_id={atom.provenance.item_id})"
                )

        # 决策账本中的 parent_source_ids 也必须闭环溯源
        if res.resolution_report:
            for dec in res.resolution_report.decisions:
                self.assertGreater(len(dec.parent_source_ids), 0)
                for pid in dec.parent_source_ids:
                    is_valid_trace = (
                        pid in self.all_catalog_ids
                        or pid in source_atom_ids
                        or pid in source_item_ids
                        or pid.startswith("linkage_")
                        or pid.startswith("clothing:")
                        or pid.startswith("rule:")
                    )
                    self.assertTrue(
                        is_valid_trace,
                        f"Orphan parent_source_id '{pid}' in decision {dec.decision_id}"
                    )


if __name__ == "__main__":
    unittest.main()
