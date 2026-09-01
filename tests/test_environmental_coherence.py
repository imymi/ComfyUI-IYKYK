"""
test_environmental_coherence.py — 白昼黑夜环境互斥与素颜妆容自洽测试
"""
from __future__ import annotations

import unittest
from pathlib import Path
from random import Random

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptFragment


class TestEnvironmentalCoherence(unittest.TestCase):
    """测试多维物理环境（白昼 vs 黑夜）与妆容细节自洽"""

    @classmethod
    def setUpClass(cls):
        cls.resolver = ConflictResolver(Path(__file__).parent.parent / "data")

    def test_night_scene_anchor_preserved_and_daylight_lighting_purged(self):
        """主场景为夜景（如 hotel balcony night / park at night）时，必须保留主场景锚点，剔除冲突的日间光影"""
        frags = [
            PromptFragment(text="hotel balcony night, night skyline outside window", source_slot="scene_theme", order=1),
            PromptFragment(text="natural daylight, morning sunlight", source_slot="lighting", order=2),
            PromptFragment(text="japanese girl", source_slot="character", order=3),
        ]

        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)

        # 断言主场景保留
        self.assertIn("hotel balcony night", resolved_text)
        # 断言冲突的日间光照被消解
        self.assertNotIn("natural daylight", resolved_text)
        self.assertNotIn("morning sunlight", resolved_text)

    def test_daylight_purges_incidental_night_elements(self):
        """日间场景与自然日光下，消解器必须剔除偶发的非主场景黑夜元素"""
        frags = [
            PromptFragment(text="bright sunlit room, window view", source_slot="scene_theme", order=1),
            PromptFragment(text="natural daylight, morning sunlight", source_slot="lighting", order=2),
            PromptFragment(text="deep dark night, pitch black background", source_slot="lighting", order=3),
        ]

        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)

        self.assertNotIn("deep dark night", resolved_text)
        self.assertNotIn("pitch black background", resolved_text)
        self.assertIn("natural daylight", resolved_text)

    def test_no_makeup_purges_smudged_mascara_and_lipstick(self):
        """真实词库中裸肌/清纯妆容状态下，消解器必须剔除脸颊唇膏蹭花、眼线晕开等糊妆矛盾词条"""
        frags = [
            PromptFragment(text="natural makeup, pure face, clean beauty", source_slot="makeup", order=1),
            PromptFragment(text="smeared lipstick on cheek, smudged eyeliner", source_slot="makeup", order=2),
        ]

        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)

        self.assertNotIn("smeared lipstick on cheek", resolved_text)
        self.assertNotIn("smudged eyeliner", resolved_text)
        self.assertIn("natural makeup", resolved_text)


if __name__ == "__main__":
    unittest.main()
