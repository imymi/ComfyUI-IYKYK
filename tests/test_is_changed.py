import math
import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from nodes import IYKYKPromptGenerator, IYKYKPresetBrowser, IYKYKCustomSlotCombiner


class TestIsChanged(unittest.TestCase):
    def test_prompt_generator_is_changed(self):
        # 1. seed = -1 returns NaN
        res_nan = IYKYKPromptGenerator.IS_CHANGED(prompt_seed=-1, 场景大类="教室", 服装款式="JK制服")
        self.assertTrue(math.isnan(res_nan), "prompt_seed=-1 must return NaN")

        # 2. Fixed seed returns deterministic hash
        h1 = IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42, 场景大类="教室", 服装款式="JK制服")
        h2 = IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42, 场景大类="教室", 服装款式="JK制服")
        self.assertIsInstance(h1, str)
        self.assertEqual(h1, h2, "Same inputs must produce identical hash")

        # 3. Changing an input changes the hash
        h3 = IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42, 场景大类="办公室", 服装款式="JK制服")
        self.assertNotEqual(h1, h3, "Different inputs must produce different hashes")

        # 4. Max 64-bit seed works without overflow
        h_max = IYKYKPromptGenerator.IS_CHANGED(prompt_seed=0xFFFFFFFFFFFFFFFF, 场景大类="教室")
        self.assertIsInstance(h_max, str)

    def test_preset_browser_is_changed(self):
        res_nan = IYKYKPresetBrowser.IS_CHANGED(prompt_seed=-1, 预设模板="01 温泉旅馆·初夜")
        self.assertTrue(math.isnan(res_nan))

        h1 = IYKYKPresetBrowser.IS_CHANGED(prompt_seed=123, 预设模板="01 温泉旅馆·初夜")
        h2 = IYKYKPresetBrowser.IS_CHANGED(prompt_seed=123, 预设模板="01 温泉旅馆·初夜")
        self.assertEqual(h1, h2)

        h3 = IYKYKPresetBrowser.IS_CHANGED(prompt_seed=123, 预设模板="02 教室后排")
        self.assertNotEqual(h1, h3)

    def test_custom_combiner_is_changed(self):
        res_nan = IYKYKCustomSlotCombiner.IS_CHANGED(prompt_seed=-1, 场景主题="onsen")
        self.assertTrue(math.isnan(res_nan))

        h1 = IYKYKCustomSlotCombiner.IS_CHANGED(prompt_seed=999, 场景主题="onsen")
        h2 = IYKYKCustomSlotCombiner.IS_CHANGED(prompt_seed=999, 场景主题="onsen")
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
