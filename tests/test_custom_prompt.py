"""Custom prompt integration across generation paths."""
import json
import unittest

from nodes import IYKYKPresetBrowser, IYKYKPromptDiagnostics, IYKYKPromptGenerator, _sampler


class TestCustomPrompt(unittest.TestCase):
    def test_generation_paths_preserve_custom_tags_and_empty_compatibility(self):
        generator = IYKYKPromptGenerator()
        browser = IYKYKPresetBrowser()
        preset = _sampler.list_preset_names()[0]
        calls = [
            lambda **kw: generator.generate(prompt_seed=42, **kw),
            lambda **kw: generator.generate(预设模板=preset, prompt_seed=42, **kw),
            lambda **kw: browser.browse(preset, "无 (None)", "高清写真 (High)", 42, **kw),
        ]
        custom = '<lora:custom_test:0.7>, (iridescent backdrop:1.2)'
        for call in calls:
            with self.subTest(call=call):
                baseline = call()
                self.assertEqual(baseline, call(自定义提示词=""))
                self.assertEqual(baseline, call(自定义提示词=" \n "))
                result = call(自定义提示词=custom)
                self.assertEqual(result, call(自定义提示词=custom))
                self.assertIn('<lora:custom_test:0.7>', result[0])
                self.assertIn('(iridescent backdrop:1.2)', result[0])
                self.assertEqual(result[1:], baseline[1:])

    def test_diagnostics_and_cache_include_custom_input(self):
        inputs = dict(prompt_seed=42, 自定义提示词="iridescent backdrop, iridescent backdrop")
        expected = IYKYKPromptGenerator().generate(**inputs)
        actual = IYKYKPromptDiagnostics().diagnose(**inputs)
        self.assertEqual(actual[:3], expected)
        self.assertEqual(expected[0].count("iridescent backdrop"), 1)
        audit = json.loads(actual[3])
        custom = [s for s in audit['selections'] if s['selector'] == 'custom']
        self.assertEqual(len(custom), 1)
        self.assertEqual(custom[0]['mode'], 'custom')
        self.assertTrue(custom[0]['deduplicated_records'])
        self.assertNotEqual(
            IYKYKPromptGenerator.IS_CHANGED(**inputs),
            IYKYKPromptGenerator.IS_CHANGED(prompt_seed=42, 自定义提示词="different backdrop"),
        )
