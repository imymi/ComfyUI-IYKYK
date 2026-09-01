import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from nodes import IYKYKCustomSlotCombiner


class TestSpatialCoherence(unittest.TestCase):
    def setUp(self):
        self.combiner = IYKYKCustomSlotCombiner()

    def test_onsen_vs_dining_resolution(self):
        """When onsen is declared first, dining venues should be purged."""
        pos, _, _ = self.combiner.combine(
            prompt_seed=42,
            场景主题="indoor onsen, onsen changing room, onsen with snow view, love hotel restaurant, cafe booth, food stall with curtain",
        )
        pos_lower = pos.lower()
        self.assertNotIn("love hotel restaurant", pos_lower)
        self.assertNotIn("cafe booth", pos_lower)
        self.assertNotIn("food stall with curtain", pos_lower)

    def test_outdoor_riverbank_vs_spinning_room(self):
        """Behind bushes and embankment should purge indoor room and turn spinning room to drunken stupor."""
        pos, _, _ = self.combiner.combine(
            prompt_seed=42,
            场景主题="behind bushes, embankment, under bridge, spinning room",
        )
        pos_lower = pos.lower()
        self.assertNotIn("spinning room", pos_lower)
        self.assertIn("drunken stupor", pos_lower)

    def test_classroom_vs_izakaya(self):
        """School classroom declared first should purge izakaya booth."""
        pos, _, _ = self.combiner.combine(
            prompt_seed=42,
            场景主题="empty classroom, blackboard, desk by window, izakaya booth",
            服装款式="school uniform",
        )
        pos_lower = pos.lower()
        self.assertIn("classroom", pos_lower)
        self.assertNotIn("izakaya", pos_lower)


if __name__ == "__main__":
    unittest.main()
