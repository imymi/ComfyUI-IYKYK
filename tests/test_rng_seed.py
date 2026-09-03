import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from nodes import IYKYKPromptGenerator, IYKYKPresetBrowser, IYKYKCustomSlotCombiner


class TestRNGSeedReproducibility(unittest.TestCase):
    def setUp(self):
        self.generator = IYKYKPromptGenerator()
        self.browser = IYKYKPresetBrowser()
        self.combiner = IYKYKCustomSlotCombiner()

    def test_generator_500_seeds_deterministic(self):
        """Test that 500 distinct seeds produce 100% identical outputs when re-run with same seed."""
        for seed in range(500):
            run1 = self.generator.generate(
                prompt_seed=seed,
                预设模板="无 (None)",
                风格配方="随机 (Random)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="自动 (Auto)",
                拍摄视角="自动 (Auto)",
                裸露等级="随机 (Random)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                发型发色="随机 (Random)",
                饰品头饰="随机 (Random)",
                妆容细节="随机 (Random)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="随机 (Random)",
                液体效果="随机 (Random)",
                纹身标记="随机 (Random)",
                道具物件="随机 (Random)",
                角色设定="随机 (Random)",
                真实微瑕="随机 (Random)",
                画质等级="高清写真 (High)",
            )
            run2 = self.generator.generate(
                prompt_seed=seed,
                预设模板="无 (None)",
                风格配方="随机 (Random)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="自动 (Auto)",
                拍摄视角="自动 (Auto)",
                裸露等级="随机 (Random)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                发型发色="随机 (Random)",
                饰品头饰="随机 (Random)",
                妆容细节="随机 (Random)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="随机 (Random)",
                液体效果="随机 (Random)",
                纹身标记="随机 (Random)",
                道具物件="随机 (Random)",
                角色设定="随机 (Random)",
                真实微瑕="随机 (Random)",
                画质等级="高清写真 (High)",
            )
            self.assertEqual(run1[0], run2[0], f"Positive prompt mismatch at seed {seed}")
            self.assertEqual(run1[1], run2[1], f"Negative prompt mismatch at seed {seed}")
            self.assertEqual(run1[2], run2[2], f"Description mismatch at seed {seed}")

    def test_preset_browser_deterministic(self):
        for seed in [7, 42, 100, 2026, 99999]:
            run1 = self.browser.browse(
                prompt_seed=seed,
                预设模板="C01 (教室后排露出)",
                风格配方="随机 (Random)",
                画质等级="高清写真 (High)",
            )
            run2 = self.browser.browse(
                prompt_seed=seed,
                预设模板="C01 (教室后排露出)",
                风格配方="随机 (Random)",
                画质等级="高清写真 (High)",
            )

            self.assertEqual(run1[0], run2[0], f"Preset prompt mismatch at seed {seed}")


if __name__ == "__main__":
    unittest.main()
