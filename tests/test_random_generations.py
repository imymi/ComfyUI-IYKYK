import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from nodes import IYKYKPromptGenerator


class TestRandomGenerations(unittest.TestCase):
    def setUp(self):
        self.generator = IYKYKPromptGenerator()

    def test_1000_random_generations_validity(self):
        """Test 1,000 random generations for non-empty, <=250 words, and no exceptions."""
        for seed in range(1000):
            pos, neg, desc = self.generator.generate(
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
            self.assertTrue(pos, f"Empty positive prompt at seed {seed}")
            self.assertTrue(neg, f"Empty negative prompt at seed {seed}")
            self.assertTrue(desc, f"Empty description at seed {seed}")
            word_count = len(pos.split())
            self.assertLessEqual(word_count, 250, f"Word count exceeded 250 at seed {seed} ({word_count} words)")


if __name__ == "__main__":
    unittest.main()
