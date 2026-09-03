"""
test_nudity_levels.py — 裸露等级 L1-L6 与服装联动、冲突消解专项测试套件
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from nodes import IYKYKPromptGenerator
from lib.sampler import DataSampler


class TestNudityLevels(unittest.TestCase):
    """测试 6 级裸露梯度与服装联动、冲突消解"""

    @classmethod
    def setUpClass(cls):
        cls.generator = IYKYKPromptGenerator()
        cls.sampler = DataSampler(Path(__file__).parent.parent / "data")
        cls.all_styles = cls.sampler.list_clothing_styles()
        cls.all_states = ["自动联动裸露等级 (Auto Link Nudity)", "随机 (Random)"] + cls.sampler.list_clothing_states()

    def test_regex_fails_on_injected_violation(self):
        """自检测试：断言向 L1 提示词注入已知违规词时，正则必定能够成功捕获"""
        banned_patterns = [
            r"\bskirt pulled up\b",
            r"\bskirt hiked up\b",
            r"\brevealing panties\b",
            r"\bshowing panties\b",
            r"\bpanties\b",
            r"\btopless\b",
            r"\bbare breasts\b",
            r"\bpussy\b",
            r"\bnude\b",
            r"\bnaked\b",
            r"\bspread legs\b",
        ]
        sample_clean_l1 = "indoor bedroom, neatly dressed, tight-fitting silk qipao, high mandarin collar, best quality"
        for pat in banned_patterns:
            raw_word = pat.replace(r"\b", "")
            dirty_prompt = f"{sample_clean_l1}, {raw_word}"
            self.assertTrue(
                bool(re.search(pat, dirty_prompt, re.IGNORECASE)),
                f"Pattern [{pat}] failed to match injected violation [{raw_word}]!"
            )

    def test_no_c0_control_characters_in_source(self):
        """断言代码与数据目录中不存在意外的 C0 控制字符（除 \\n, \\r, \\t）"""
        root = Path(__file__).parent.parent
        allowed_chars = {9, 10, 13}  # \t, \n, \r
        for folder in ["data", "lib", "tests", "js"]:
            p = root / folder
            if not p.exists():
                continue
            for file_path in p.rglob("*.*"):
                if file_path.suffix not in [".py", ".json", ".js", ".md"]:
                    continue
                content_bytes = file_path.read_bytes()
                for idx, byte_val in enumerate(content_bytes):
                    if byte_val < 32 and byte_val not in allowed_chars:
                        self.fail(
                            f"Found C0 control character 0x{byte_val:02x} in {file_path} at byte offset {idx}"
                        )

    def test_l1_zero_exposure_in_random_sampling(self):
        """测试 500 次 L1 随机生成，严格断言绝不出现任何露内裤、掀裙、露乳、全裸等词条"""
        banned_patterns = [
            r"\bskirt pulled up\b",
            r"\bskirt hiked up\b",
            r"\bskirt lifted\b",
            r"\bskirt riding up\b",
            r"\brevealing panties\b",
            r"\bshowing panties\b",
            r"\bpanties visible\b",
            r"\bvisible panties\b",
            r"\bpanties\b",
            r"\bbra\b",
            r"\btopless\b",
            r"\bbare breasts\b",
            r"\bexposed breasts\b",
            r"\bcleavage\b",
            r"\bunderboob\b",
            r"\bsideboob\b",
            r"\bpussy\b",
            r"\bnude\b",
            r"\bnaked\b",
            r"\bspread legs\b",
            r"\bundressed\b",
            r"\bupskirt\b",
        ]

        for seed in range(500):
            pos, neg, desc = self.generator.generate(
                预设模板="无 (None)",
                风格配方="无 (None)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="随机 (Random)",
                拍摄视角="自动 (Auto)",
                裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                发型发色="随机 (Random)",
                饰品头饰="无 (None)",
                妆容细节="无 (None)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="无 (None)",
                液体效果="无 (None)",
                纹身标记="无 (None)",
                道具物件="无 (None)",
                角色设定="无 (None)",
                真实微瑕="无 (None)",
                画质等级="高清写真 (High)",
                prompt_seed=seed,
            )

            for pat in banned_patterns:
                self.assertFalse(
                    bool(re.search(pat, pos, re.IGNORECASE)),
                    f"Seed {seed} failed with banned pattern [{pat}] in L1: {pos}"
                )

    def test_l1_across_all_clothing_states(self):
        """测试全部 14 种服装状态在 L1 下均不会穿透产生裸露词"""
        banned_patterns = [
            r"\bskirt pulled up\b",
            r"\bskirt hiked up\b",
            r"\brevealing panties\b",
            r"\bshowing panties\b",
            r"\bpanties\b",
            r"\btopless\b",
            r"\bbare breasts\b",
            r"\bpussy\b",
            r"\bnude\b",
            r"\bnaked\b",
        ]

        for state in self.all_states:
            for seed in range(20):
                pos, neg, desc = self.generator.generate(
                    预设模板="无 (None)",
                    风格配方="无 (None)",
                    场景大类="随机 (Random)",
                    剧情主题="随机 (Random)",
                    景别构图="随机 (Random)",
                    拍摄视角="自动 (Auto)",
                    裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                    服装款式="随机 (Random)",
                    服装状态=state,
                    发型发色="随机 (Random)",
                    饰品头饰="无 (None)",
                    妆容细节="无 (None)",
                    姿势动作="随机 (Random)",
                    情绪表情="随机 (Random)",
                    光影预设="自动 (Auto)",
                    胶片风格="无 (None)",
                    液体效果="无 (None)",
                    纹身标记="无 (None)",
                    道具物件="无 (None)",
                    角色设定="无 (None)",
                    真实微瑕="无 (None)",
                    画质等级="高清写真 (High)",
                    prompt_seed=seed,
                )

                for pat in banned_patterns:
                    self.assertFalse(
                        bool(re.search(pat, pos, re.IGNORECASE)),
                        f"State [{state}] Seed {seed} violated L1 purity: {pos}"
                    )

    def test_all_28_clothing_styles_across_l1_to_l6_matrix(self):
        """测试 28 种服装款式 × 6 种裸露等级 = 168 种组合均可正常联动且无自相矛盾"""
        nudity_levels = [
            ("L1", "L1 包裹暗示 (Fully Clothed / Suggestive)"),
            ("L2", "L2 差分微露 (Partially Exposed)"),
            ("L3", "L3 半裸诱惑 (Half Nude)"),
            ("L4", "L4 极简遮挡 (Topless / Bottomless)"),
            ("L5", "L5 极致全裸 (Full Nude)"),
            ("L6", "L6 特写全见 (Explicit Genital Close-up)"),
        ]

        for lvl_id, lvl_name in nudity_levels:
            for style in self.all_styles:
                pos, neg, desc = self.generator.generate(
                    预设模板="无 (None)",
                    风格配方="无 (None)",
                    场景大类="随机 (Random)",
                    剧情主题="无 (None)",
                    景别构图="自动 (Auto)",
                    拍摄视角="自动 (Auto)",
                    裸露等级=lvl_name,
                    服装款式=style,
                    服装状态="自动联动裸露等级 (Auto Link Nudity)",
                    发型发色="随机 (Random)",
                    饰品头饰="无 (None)",
                    妆容细节="无 (None)",
                    姿势动作="随机 (Random)",
                    情绪表情="随机 (Random)",
                    光影预设="自动 (Auto)",
                    胶片风格="无 (None)",
                    液体效果="无 (None)",
                    纹身标记="无 (None)",
                    道具物件="无 (None)",
                    角色设定="无 (None)",
                    真实微瑕="无 (None)",
                    画质等级="高清写真 (High)",
                    prompt_seed=12345,
                )

                self.assertTrue(len(pos.split()) >= 3, f"{lvl_id} + {style} produced empty prompt")

                if lvl_id == "L1":
                    self.assertFalse(
                        any(re.search(b, pos, re.IGNORECASE) for b in [r"\btopless\b", r"\bbare breasts\b", r"\bpussy\b", r"\bnude\b", r"\bnaked\b", r"\bpanties\b"]),
                        f"L1 + {style} contained exposed tags: {pos}"
                    )
                elif lvl_id == "L5":
                    self.assertFalse(
                        any(re.search(b, pos, re.IGNORECASE) for b in [r"\bwearing skirt\b", r"\bwearing dress\b", r"\bwearing blazer\b", r"\bwearing blouse\b", r"\bfully clothed\b", r"\bneatly dressed\b"]),
                        f"L5 + {style} contained wearing clothing tags: {pos}"
                    )


if __name__ == "__main__":
    unittest.main()
