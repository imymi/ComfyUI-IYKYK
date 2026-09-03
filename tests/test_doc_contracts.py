"""
test_doc_contracts.py — 文档与元数据单源契约自动化测试 (P2-4)

验证 README.md、CHANGELOG.md 与 __init__.py 中引用的代码常量、
规则 ID、槽位定义及 generation 命名模板与代码实现保持 100% 吻合，防漂移。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from lib.rule_contract import STABLE_RULE_ORDER
from lib.slot_contract import AUXILIARY_SLOT_ORDER, SLOT_ORDER
from nodes import IYKYKPromptGenerator


class TestDocContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_dir = Path(__file__).resolve().parent.parent
        cls.readme_text = (cls.repo_dir / "README.md").read_text(encoding="utf-8")
        cls.changelog_text = (cls.repo_dir / "CHANGELOG.md").read_text(encoding="utf-8")
        cls.init_text = (cls.repo_dir / "__init__.py").read_text(encoding="utf-8")
        cls.build_release_text = (cls.repo_dir / "scripts" / "build_release.py").read_text(encoding="utf-8")

    def test_readme_slot_constants_and_rules(self):
        """断言 README 中引用的槽位常量、规则 ID 与代码实现完全对齐"""
        # 1. 槽位常量存在性
        self.assertIn("SLOT_ORDER", self.readme_text)
        self.assertIn("AUXILIARY_SLOT_ORDER", self.readme_text)
        self.assertNotIn("SLOT_PIPELINE_ORDER", self.readme_text)

        # 2. 17 大规则 ID 逐一出现在 README 中
        for rid in STABLE_RULE_ORDER:
            self.assertIn(rid, self.readme_text, f"Rule ID '{rid}' missing in README.md")

        # 3. tattoo 规则 ID 为 tattoo_dermal_fusion
        self.assertIn("tattoo_dermal_fusion", self.readme_text)
        self.assertNotIn("`tattoo_fusion`", self.readme_text)

    def test_generator_input_types_match_doc(self):
        """断言 IYKYKPromptGenerator 的输入端口与 README 参数文档一致"""
        inputs = IYKYKPromptGenerator.INPUT_TYPES()
        required_names = set(inputs["required"].keys())

        # 验证 README 包含了全部核心中文端口
        for port in required_names:
            self.assertIn(f"`{port}`", self.readme_text, f"Port '{port}' missing in README table")

        # 确认主生成器不包含已废弃的旧英文参数名
        for old_eng in ("`scene`", "`theme`", "`nudity_level`", "`clothing_style`", "`custom_tags`"):
            self.assertNotIn(f"| {old_eng} |", self.readme_text)

    def test_registered_nodes_and_combiner_ports_match_doc(self):
        """断言 NODE_CLASS_MAPPINGS 注册节点与 IYKYKCustomSlotCombiner 端口与 README 严格对齐 (P2-4)"""
        from nodes import NODE_CLASS_MAPPINGS, IYKYKCustomSlotCombiner

        # 1. 全部已注册节点类名必须出现在 README 中
        for node_name in NODE_CLASS_MAPPINGS:
            self.assertIn(f"`{node_name}`", self.readme_text, f"Registered node '{node_name}' missing in README")

        # 2. 严禁出现虚构或未注册的旧节点名
        self.assertNotIn("IYKYKStructuredCombiner", self.readme_text)

        # 3. 严禁出现未注册的旧端口名 custom_tags
        self.assertNotIn("custom_tags", self.readme_text)

        # 4. IYKYKCustomSlotCombiner 真实端口 '自定义追加' 必须出现在 README 中
        combiner_inputs = IYKYKCustomSlotCombiner.INPUT_TYPES()["optional"]
        self.assertIn("自定义追加", combiner_inputs)
        self.assertIn("`自定义追加`", self.readme_text)

    def test_generation_directory_naming_contract(self):
        """断言 CHANGELOG 与构建脚本中不可变 generation 目录格式一致"""
        # 代码中使用的命名格式
        self.assertIn('target_gen_name = f"v{version}-{mode}-{archive_sha12}"', self.build_release_text)
        # CHANGELOG 中文档一致
        self.assertIn('f"v{version}-{mode}-{archive_sha12}"', self.changelog_text)

    def test_init_docstring_counts(self):
        """断言 __init__.py 中的槽位与规则数量描述与代码常量一致"""
        self.assertIn("18 核心 + 2 辅助槽位流水线", self.init_text)
        self.assertIn(f"{len(STABLE_RULE_ORDER)} 大冲突消解引擎", self.init_text)
        self.assertEqual(len(SLOT_ORDER), 18)
        self.assertEqual(len(AUXILIARY_SLOT_ORDER), 2)
        self.assertEqual(len(STABLE_RULE_ORDER), 17)


if __name__ == "__main__":
    unittest.main()
