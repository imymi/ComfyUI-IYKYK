"""
test_lexer_and_spans.py — 生产级共享 Lexer、PromptAtom 模型、Span 权限矩阵与 250 词预算管理测试
"""
from __future__ import annotations

import unittest
from pathlib import Path

from lib.assembler import PromptAssembler, assemble_prompt, finalize_prompt, finalize_prompt_atoms, sanitize_prompt
from lib.errors import PromptSyntaxError, PromptValidationError
from lib.lexer import (
    ParsedPrompt,
    ParsedTag,
    PromptSpan,
    SpanType,
    parse_prompt,
    split_top_level_tags,
    tokenize_prompt_spans,
    validate_prompt_syntax,
)
from lib.models import PromptAtom, PromptFragment, TagProvenance


class TestLexerAndSpans(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(__file__).parent.parent
        self.data_dir = self.repo_dir / "data"

    def test_split_top_level_tags_complex(self):
        """验证顶层逗号拆分正确保护各类嵌套与转义"""
        raw = r'1girl, (solo:1.2, detailed face:1.1), [day:night:10], <lora:detail_v1:0.8>, "exact phrase, with comma", escaped\,tag'
        tags = split_top_level_tags(raw)
        self.assertEqual(len(tags), 6)
        self.assertEqual(tags[0], "1girl")
        self.assertEqual(tags[1], "(solo:1.2, detailed face:1.1)")
        self.assertEqual(tags[2], "[day:night:10]")
        self.assertEqual(tags[3], "<lora:detail_v1:0.8>")
        self.assertEqual(tags[4], '"exact phrase, with comma"')
        self.assertEqual(tags[5], r"escaped\,tag")

    def test_validate_prompt_syntax_stack(self):
        """验证严格的 LIFO 栈式语法校验"""
        # 合法语法
        validate_prompt_syntax('1girl, (masterpiece:1.2), [a:b:5], <lora:test:1>, "quoted"')

        # 交叉嵌套错误 ([)] / [(])
        with self.assertRaises(PromptSyntaxError):
            validate_prompt_syntax("([)]")

        with self.assertRaises(PromptSyntaxError):
            validate_prompt_syntax("[(])")

        # 未闭合结构
        with self.assertRaises(PromptSyntaxError):
            validate_prompt_syntax("(unclosed paren")

        with self.assertRaises(PromptSyntaxError):
            validate_prompt_syntax("[unclosed bracket")

        with self.assertRaises(PromptSyntaxError):
            validate_prompt_syntax("<lora:unclosed")

        with self.assertRaises(PromptSyntaxError):
            validate_prompt_syntax('"unclosed quote')

        with self.assertRaises(PromptSyntaxError):
            validate_prompt_syntax("trailing backslash" + chr(92))

    def test_tokenize_prompt_spans_syntax_fail_fast(self):
        """验证 Tokenizer 对未闭合结构与裸露反斜杠 Fail-Fast 抛出 PromptSyntaxError"""
        with self.assertRaises(PromptSyntaxError):
            tokenize_prompt_spans('valid prefix, "unclosed quote')

        with self.assertRaises(PromptSyntaxError):
            tokenize_prompt_spans("valid prefix, <lora:unclosed_angle")

        with self.assertRaises(PromptSyntaxError):
            tokenize_prompt_spans("valid prefix, (unclosed_paren")

        with self.assertRaises(PromptSyntaxError):
            tokenize_prompt_spans("valid prefix, [unclosed_bracket")

        with self.assertRaises(PromptSyntaxError):
            tokenize_prompt_spans("valid prefix, trailing" + chr(92))

    def test_span_permissions(self):
        """验证六大 Span 类型的可检测、可改写、可删除权限矩阵"""
        plain_span = PromptSpan("classroom", SpanType.PLAIN)
        self.assertTrue(plain_span.can_detect)
        self.assertTrue(plain_span.can_modify_internal)
        self.assertTrue(plain_span.can_delete_atom)

        paren_span = PromptSpan("(holding cup:1.2)", SpanType.PAREN)
        self.assertTrue(paren_span.can_detect)
        self.assertFalse(paren_span.can_modify_internal)
        self.assertTrue(paren_span.can_delete_atom)

        bracket_span = PromptSpan("[day:night:10]", SpanType.BRACKET)
        self.assertTrue(bracket_span.can_detect)
        self.assertFalse(bracket_span.can_modify_internal)
        self.assertTrue(bracket_span.can_delete_atom)

        angle_span = PromptSpan("<lora:add_detail:0.8>", SpanType.ANGLE)
        self.assertFalse(angle_span.can_detect)
        self.assertFalse(angle_span.can_modify_internal)
        self.assertFalse(angle_span.can_delete_atom)

        quoted_span = PromptSpan('"exact phrase"', SpanType.QUOTED)
        self.assertFalse(quoted_span.can_detect)
        self.assertFalse(quoted_span.can_modify_internal)
        self.assertFalse(quoted_span.can_delete_atom)

    def test_mixed_protected_spans_byte_preservation(self):
        """
        核心不变量测试 (复核关键要求)：
        验证在混合片段中，LoRA、双引号短语、权重括号内部字节 100% 原样保留，
        绝不被 conflict resolver 的规则 (如 spinning room -> drunken stupor) 误改写！
        而同片段/外部普通文本中的 spinning room 则正确被替换为 drunken stupor。
        """
        mixed_cases = [
            # 1. 普通文本 + LoRA: spinning room 在 LoRA 内部必须绝对不变
            (
                "prefix spinning room <lora:spinning room:1.0> suffix",
                "<lora:spinning room:1.0>",
                "drunken stupor",
            ),
            # 2. 普通文本 + 双引号: spinning room 在引号内部必须绝对不变
            (
                'prefix spinning room "spinning room" suffix',
                '"spinning room"',
                "drunken stupor",
            ),
            # 3. 普通文本 + 权重括号: spinning room 在权重括号内部禁止改写内部字节
            (
                "prefix spinning room (spinning room:1.2) suffix",
                "(spinning room:1.2)",
                "drunken stupor",
            ),
        ]

        for input_text, expected_preserved_span, expected_replaced_text in mixed_cases:
            frags = [PromptFragment(text=input_text, source_slot="scene_theme")]
            rendered = assemble_prompt(frags, data_dir=self.data_dir)

            # 断言受保护的 span 字节级完全原样保留
            self.assertIn(expected_preserved_span, rendered, f"Protected span was modified in: {rendered}")
            # 断言外部普通文本正确发生了冲突消解替换
            self.assertIn(expected_replaced_text, rendered, f"Plain text was not replaced in: {rendered}")

    def test_budget_truncation_semantics(self):
        """
        验证 250 词硬约束：
        - 原子 span 能完整放入剩余预算：保留；
        - 放不下：整块跳过；
        - 单个原子 span 自身超过总上限：抛 PromptValidationError。
        """
        # 1. 单个原子 span 超过 max_words: 抛 PromptValidationError
        giant_span = "word " * 251
        frags = [PromptFragment(text=giant_span.strip(), source_slot="clothing")]
        with self.assertRaises(PromptValidationError):
            finalize_prompt(frags, data_dir=self.data_dir, max_words=250)

        # 2. 刚好放不下时：整块跳过，绝不切断 span 内部单词
        frags = [
            PromptFragment(text="word " * 248, source_slot="clothing"),
            PromptFragment(text="four words atomic span", source_slot="props"),
        ]
        res = finalize_prompt(frags, data_dir=self.data_dir, max_words=250)
        self.assertNotIn("four", res)
        self.assertNotIn("atomic", res)
        self.assertEqual(len(res.split()), 248)

    def test_global_monotonic_flattening(self):
        """验证组装器将复合 tag 彻底解构为原子片段，并赋予 0..N-1 的严格单调递增 order"""
        assembler = PromptAssembler(self.data_dir)
        slots = {
            "scene_theme": ["classroom, wooden desk, blackboard"],
            "clothing": ["jk_seifuku", "pleated skirt, white shirt"],
            "props": ["smartphone, backpack"],
        }
        frags = assembler.assemble_to_fragments(slots)
        self.assertEqual(len(frags), 8)
        self.assertEqual([f.text for f in frags], [
            "classroom", "wooden desk", "blackboard",
            "jk_seifuku", "pleated skirt", "white shirt",
            "smartphone", "backpack"
        ])
        orders = [f.order for f in frags]
        self.assertEqual(orders, list(range(len(frags))))

    def test_finalize_prompt_atoms_provenance_retention(self):
        """验证结构化 finalize_prompt_atoms 接口完整无损保留 PromptAtom 的来源、槽位与 provenance"""
        prov = TagProvenance(item_id="scene_001", kind="scene_anchor")
        frags = [
            PromptFragment(
                text="classroom, (blackboard:1.1)",
                source_slot="scene_theme",
                source_item_id="scene_001",
                provenance=prov,
            )
        ]
        atoms = finalize_prompt_atoms(frags, data_dir=self.data_dir)
        self.assertEqual(len(atoms), 2)
        self.assertEqual(atoms[0].text, "classroom")
        self.assertEqual(atoms[0].span_type, SpanType.PLAIN)
        self.assertEqual(atoms[0].source_slot, "scene_theme")
        self.assertEqual(atoms[0].provenance.item_id, "scene_001")
        self.assertEqual(atoms[0].provenance.kind, "scene_anchor")

        self.assertEqual(atoms[1].text, "(blackboard:1.1)")
        self.assertEqual(atoms[1].span_type, SpanType.PAREN)
        self.assertEqual(atoms[1].source_slot, "scene_theme")



    def test_nested_structures_roundtrip_and_counter_examples(self):
        """
        验证复核指出的关键反例与复杂嵌套结构：
        1. (prefix ")" suffix:1.2)
        2. (prefix <lora:x):1> suffix:1.2)
        3. [prefix "quoted ]" suffix]
        4. ([<lora:test:1.0>, highly detailed:1.2])
        5. 多个连续 protected spans
        断言每个合法输入均通过语法校验，且 Token 拼接 100% 字节恒等！
        """
        test_cases = [
            '(prefix ")" suffix:1.2)',
            '(prefix <lora:x):1> suffix:1.2)',
            '[prefix "quoted ]" suffix]',
            '([<lora:test:1.0>, highly detailed:1.2])',
            '<lora:a:1><lora:b:1>"quoted1""quoted2"',
            'prefix, (outer:1.1, [inner:1.0], <lora:x:1.0>), suffix',
            r'tag\,with\,escapes, "quote with \" escaped quote"',
        ]

        for tc in test_cases:
            # 1. 语法校验必须通过
            validate_prompt_syntax(tc)

            # 2. Tokenizer 必须成功切分
            spans = tokenize_prompt_spans(tc)

            # 3. 严格不变量：拼接必与原文本 100% 字节恒等
            rebuilt = "".join(s.text for s in spans)
            self.assertEqual(rebuilt, tc, f"Byte preservation failed! Original [{tc}] vs Rebuilt [{rebuilt}]")

    def test_parameterized_500_roundtrip_invariants(self):
        """
        性质测试：随机组合 500 组包含各类嵌套、转义、LoRA 与引号的合法结构，
        断言全部通过校验且 100% 满足 join(token.text) == original 不变量。
        """
        from random import Random
        rng = Random(42)

        components = [
            "masterpiece",
            "(solo:1.2)",
            '(holding "camera":1.1)',
            "<lora:add_detail:0.8>",
            '<lora:model_v2:1.0>',
            '"exact quoted phrase"',
            '"phrase with, comma"',
            "[morning:night:15]",
            r"escaped\,comma",
            '(prefix <lora:x):1> suffix:1.2)',
            '[tag: "bracket ] quote":5]',
        ]

        for i in range(500):
            k = rng.randint(2, 6)
            chosen = rng.sample(components, k)
            prompt = ", ".join(chosen)

            # 校验与切分
            validate_prompt_syntax(prompt)
            spans = tokenize_prompt_spans(prompt)
            rebuilt = "".join(s.text for s in spans)
            self.assertEqual(rebuilt, prompt, f"Iteration {i} failed byte invariance!")

    def test_trailing_escaped_comma_in_finalize_prompt(self):
        r"""
        验证强制反例 P1-1：合法的末尾转义逗号在 finalize_prompt 全流程中 100% 原样保留。
        测试单个 escaped\,、首尾转义、连续转义、转义接黑盒等全场景，断言 finalize_prompt(x) == x。
        """
        cases = [
            r"escaped\,",
            r"\,",
            r"beach, escaped\,",
            r"escaped\,, <lora:detail:1.0>",
            r"escaped\,, \"quoted string\"",
        ]

        for c in cases:
            # 1. 传入纯字符串
            out_str = finalize_prompt(c, data_dir=self.data_dir)
            self.assertEqual(out_str, c, f"Failed for raw string [{c}], got [{out_str}]")

            # 2. 传入 PromptFragment 结构体
            out_frag = finalize_prompt([PromptFragment(text=c, source_slot="custom")], data_dir=self.data_dir)
            self.assertEqual(out_frag, c, f"Failed for PromptFragment [{c}], got [{out_frag}]")

        # 3. 负向边界：双反斜杠加逗号 (表示字面反斜杠加普通未转义逗号)，尾随未转义逗号应被正常清理
        raw_double_bs = r"escaped\\,"
        out_cleaned = finalize_prompt(raw_double_bs, data_dir=self.data_dir)
        self.assertEqual(out_cleaned, r"escaped\\")

    def test_escaped_whitespace_and_tab_in_finalize_prompt(self):
        r"""
        验证修订 P1-3：合法转义空格 escaped\ 、转义 Tab escaped\t、转义逗号及前导转义字符，
        在 finalize_prompt 全流程中 100% 原样保留，绝不报 Trailing unescaped backslash。
        """
        cases = [
            r"escaped\ ",
            r"escaped\	",
            r"\,escaped",
            r"escaped\,",
            r"a, escaped\ , b",
            r"a, escaped\	, b",
            r"a, (classroom <lora:x:1>:1.2)",
            r"a, [classroom \"exact phrase\":1.2]",
        ]

        for c in cases:
            # 1. 传入纯字符串
            out_str = finalize_prompt(c, data_dir=self.data_dir)
            self.assertEqual(out_str, c, f"Failed for raw string [{c}], got [{out_str}]")

            # 2. 传入 PromptFragment 结构体
            out_frag = finalize_prompt([PromptFragment(text=c, source_slot="custom")], data_dir=self.data_dir)
            self.assertEqual(out_frag, c, f"Failed for PromptFragment [{c}], got [{out_frag}]")

    def test_exact_byte_round_trip_for_untouched_spans(self):
        r"""
        性质测试：对任意未被 resolver 修改的 ESCAPED / ANGLE / QUOTED span，
        断言经过 assemble_prompt_result 流程后输出字节与输入完全一致。
        """
        raw_inputs = [
            r"masterpiece, <lora:detail:0.8>, \"quoted style\", escaped\ , escaped\	, escaped\,",
            r"(masterpiece:1.2), [night:day:10], <lora:skin:1.0>, \"sharp focus\", test\,123",
        ]
        for raw in raw_inputs:
            out = finalize_prompt(raw, data_dir=self.data_dir)
            tags_in = split_top_level_tags(raw)
            tags_out = split_top_level_tags(out)
            self.assertEqual(tags_in, tags_out)

    def test_sanitize_prompt_strict_identity(self):
        r"""
        验证修订 P1-2：sanitize_prompt 对任意输入字符串严格 100% 恒等透传，
        包括包含合法转义空白 escaped\  的字符串，严禁剥离或改写任何字符。
        """
        cases = [
            r"escaped\ ",
            r"escaped\	",
            "  leading and trailing whitespace  ",
            "hello\n\tworld\r\n",
            r"1girl, (masterpiece:1.2), escaped\,",
            "",
            "   ",
        ]
        for c in cases:
            self.assertIs(sanitize_prompt(c), c, f"sanitize_prompt modified string [{c!r}]")

    def test_syntax_error_consistency_across_all_public_lexical_entrypoints(self):
        """
        验证修订 P1-2：对所有非法语法的接受/拒绝结果必须在所有公共词法入口上 100% 结论一致。
        覆盖：裸尾随反斜杠、括号内裸尾随反斜杠、引号内裸尾随反斜杠、尖括号内裸尾随反斜杠。
        """
        illegal_cases = [
            "abc\\",
            "tag1, tag2\\",
            "(abc\\)",
            "[abc\\]",
            '"abc\\"',
            "<abc\\>",
            "([)]",
            "[(])",
            "(unclosed paren",
            "[unclosed bracket",
            "<unclosed angle",
            '"unclosed quote',
        ]
        for bad in illegal_cases:
            # 1. tokenize_prompt_spans 必须抛出 PromptSyntaxError
            with self.assertRaises(PromptSyntaxError, msg=f"tokenize accepted {bad!r}"):
                tokenize_prompt_spans(bad)

            # 2. validate_prompt_syntax 必须抛出 PromptSyntaxError
            with self.assertRaises(PromptSyntaxError, msg=f"validate accepted {bad!r}"):
                validate_prompt_syntax(bad)

            # 3. split_top_level_tags 必须抛出 PromptSyntaxError
            with self.assertRaises(PromptSyntaxError, msg=f"split accepted {bad!r}"):
                split_top_level_tags(bad)

            # 4. parse_prompt 必须抛出 PromptSyntaxError
            with self.assertRaises(PromptSyntaxError, msg=f"parse accepted {bad!r}"):
                parse_prompt(bad)

    def test_parsed_tags_exact_slices_and_offsets(self):
        """
        验证修订 P1-2：ParsedTag 的 text、start_idx 与 end_idx 必须精确对应原始输入的切片，
        绝不依赖模糊 offset 或默认回退 0。
        """
        raw = r'  1girl,   (solo:1.2),   escaped\ ,   "exact phrase"  '
        parsed = parse_prompt(raw)
        self.assertEqual(len(parsed.tags), 4)

        # 验证每个 tag 的切片与索引绝对对应
        for tag in parsed.tags:
            self.assertEqual(raw[tag.start_idx:tag.end_idx], tag.text)
            self.assertEqual(raw[tag.raw_start_idx:tag.raw_end_idx], tag.raw_slice)
            self.assertEqual("".join(s.text for s in tag.spans), tag.text)

        self.assertEqual(parsed.tags[0].text, "1girl")
        self.assertEqual(parsed.tags[1].text, "(solo:1.2)")
        self.assertEqual(parsed.tags[2].text, r"escaped\ ")
        self.assertEqual(parsed.tags[3].text, '"exact phrase"')

    def test_single_parse_directly_atomizes_without_rescan(self):
        """
        性质测试：单次 parse_prompt 的产物可直接完成原子化，
        与 fragments_to_atoms 得到的结果 100% 结构一致，无需二次词法扫描。
        """
        from lib.atomizer import fragments_to_atoms

        raw = r'masterpiece, (solo:1.2), escaped\ , <lora:face:1.0>'
        frag = PromptFragment(text=raw, source_slot="art_style")

        tags, atoms = fragments_to_atoms([frag])
        parsed = parse_prompt(raw)

        self.assertEqual(len(tags), len(parsed.tags))
        for ptag, parsed_tag in zip(tags, parsed.tags):
            self.assertEqual(ptag.text, parsed_tag.text)
            self.assertEqual(len(ptag.atoms), len(parsed_tag.spans))
            for a, sp in zip(ptag.atoms, parsed_tag.spans):
                self.assertEqual(a.text, sp.text)
                self.assertEqual(a.span_type, sp.span_type)


if __name__ == "__main__":
    unittest.main()
