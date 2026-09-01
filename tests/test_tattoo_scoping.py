import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from nodes import IYKYKPromptGenerator, IYKYKCustomSlotCombiner

TATTOO_FUSION_WORDS = [
    "realistic tattoo",
    "ink embedded in dermis",
    "tattoo beneath skin surface",
    "follows body contours",
    "slightly faded edges",
    "pores visible through ink",
]


class TestTattooScoping(unittest.TestCase):
    def setUp(self):
        self.generator = IYKYKPromptGenerator()
        self.combiner = IYKYKCustomSlotCombiner()

    def test_no_tattoo_with_pink_and_drink_words(self):
        """When tattoo is None, words containing 'ink' (pink, drink, link) must NOT trigger tattoo tags."""
        pos, _, _ = self.combiner.combine(
            prompt_seed=42,
            场景主题="cafe, drinking hot coffee",
            服装款式="pink bikini, pink lace bra",
            道具物件="drink bottle, pink strawberry drink",
            纹身标记="",
        )
        pos_lower = pos.lower()
        for fw in TATTOO_FUSION_WORDS:
            self.assertNotIn(
                fw,
                pos_lower,
                f"Tattoo fusion word '{fw}' falsely triggered when tattoo was None! Prompt: {pos}",
            )

    def test_random_samples_with_none_tattoo_have_zero_tattoo_tags(self):
        """In 100 random generations with tattoo='无 (None)', zero tattoo tags should appear."""
        for seed in range(100):
            pos, _, _ = self.generator.generate(
                prompt_seed=seed,
                预设模板="无 (None)",
                风格配方="无 (None)",
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
                纹身标记="无 (None)",
                道具物件="随机 (Random)",
                角色设定="随机 (Random)",
                真实微瑕="随机 (Random)",
                画质等级="高清写真 (High)",
            )
            pos_lower = pos.lower()
            for fw in TATTOO_FUSION_WORDS:
                self.assertNotIn(
                    fw,
                    pos_lower,
                    f"Tattoo fusion word '{fw}' falsely triggered at seed {seed}! Prompt: {pos}",
                )


if __name__ == "__main__":
    unittest.main()
