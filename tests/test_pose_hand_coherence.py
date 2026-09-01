"""
test_pose_hand_coherence.py — 姿态手部占用与手持道具/动作防冲突测试
"""
from __future__ import annotations

import unittest
from pathlib import Path
from random import Random

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptFragment
from nodes import IYKYKPromptGenerator


class TestPoseHandCoherence(unittest.TestCase):
    """测试姿态手部占用（双手忙/剧烈动作）与手持物互斥"""

    @classmethod
    def setUpClass(cls):
        cls.resolver = ConflictResolver(Path(__file__).parent.parent / "data")
        cls.generator = IYKYKPromptGenerator()

    def test_busy_pose_purges_handheld_props_in_resolver(self):
        """当姿势为双手背后/四肢着地/抓床单时，消解器必须剔除真实词库中的手持手机、手柄、相机、团扇等动作"""
        busy_poses = [
            "hands behind back",
            "on all fours",
            "on hands and knees",
            "gripping sheets",
            "arms above head",
            "pulling shirt over head",
        ]

        real_catalog_handheld_props = [
            "compact camera in hand",
            "arms wrapped around cute stuffed animal",
            "smartphone in hand recording",
            "holding game controller",
            "holding black compact digital camera",
            "holding embroidered round silk fan",
            "holding oiled paper umbrella propped on shoulder",
            "holding lush fresh floral bouquet",
            "hugging large fluffy plush teddy bear",
        ]

        for bp in busy_poses:
            for hp in real_catalog_handheld_props:
                frags = [
                    PromptFragment(text="indoor studio", source_slot="scene_theme", order=1),
                    PromptFragment(text=bp, source_slot="pose", order=2),
                    PromptFragment(text=hp, source_slot="props", order=3),
                    PromptFragment(text="best quality", source_slot="quality", order=4),
                ]

                resolved = self.resolver.resolve_fragments(frags, Random(42))
                resolved_text = " ".join(f.text.lower() for f in resolved)

                self.assertNotIn(
                    hp.lower(),
                    resolved_text,
                    f"Pose [{bp}] failed to purge handheld prop [{hp}]!"
                )
                self.assertIn(bp.lower(), resolved_text)

    def test_ambient_props_retained_with_busy_pose(self):
        """当姿势为双手占用时，非手持的背景道具（如床头酒杯、散落花瓣、猫咪）应被正常保留"""
        frags = [
            PromptFragment(text="hands behind back", source_slot="pose", order=1),
            PromptFragment(text="half-empty wine glass on nightstand", source_slot="props", order=2),
            PromptFragment(text="scattered red rose petals on sheets", source_slot="props", order=3),
            PromptFragment(text="fluffy cat sleeping on corner of bed", source_slot="props", order=4),
        ]

        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)

        self.assertIn("half-empty wine glass on nightstand", resolved_text)
        self.assertIn("scattered red rose petals on sheets", resolved_text)
        self.assertIn("fluffy cat sleeping on corner of bed", resolved_text)


if __name__ == "__main__":
    unittest.main()
