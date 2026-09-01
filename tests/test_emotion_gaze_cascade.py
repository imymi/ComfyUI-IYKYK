"""
test_emotion_gaze_cascade.py — 情绪表情与眼神方向动态级联与一致性测试
"""
from __future__ import annotations

import unittest
from pathlib import Path
from random import Random

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptFragment


class TestEmotionGazeCascade(unittest.TestCase):
    """测试情绪表情与视线/眼神方向一致性"""

    @classmethod
    def setUpClass(cls):
        cls.resolver = ConflictResolver(Path(__file__).parent.parent / "data")

    def test_shy_emotion_purges_dominant_and_bold_stare(self):
        """害羞/羞怯情绪下，消解器必须剔除真实词库中的掠夺性挑逗、大胆直视、魅惑笑容等矛盾眼神与表情"""
        for banned_gaze in ["predatory inviting gaze", "seductive smile", "sultry gaze", "eye-fucking"]:
            frags = [
                PromptFragment(text="shy expression, blushing cheeks", source_slot="expression", order=1),
                PromptFragment(text=banned_gaze, source_slot="camera_angle", order=2),
                PromptFragment(text="soft indoor light", source_slot="lighting", order=3),
            ]

            resolved = self.resolver.resolve_fragments(frags, Random(42))
            resolved_text = " ".join(f.text.lower() for f in resolved)

            self.assertNotIn(banned_gaze, resolved_text, f"Shy emotion failed to purge [{banned_gaze}]!")
            self.assertIn("shy expression", resolved_text)

    def test_cold_deadpan_emotion_purges_flirty_wink(self):
        """冷淡/无表情下，消解器必须剔除俏皮眨眼、甜笑等割裂眼神"""
        frags = [
            PromptFragment(text="bored expression, deadpan face", source_slot="expression", order=1),
            PromptFragment(text="playful wink, flirty wink", source_slot="camera_angle", order=2),
        ]

        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)

        self.assertNotIn("flirty wink", resolved_text)
        self.assertIn("deadpan face", resolved_text)


if __name__ == "__main__":
    unittest.main()
