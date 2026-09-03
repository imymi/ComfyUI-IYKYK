"""
lexer.py — 生产级统一提示词词法解析器与语法状态机

1. 共享单遍状态机：统一管理转义、双引号、尖括号与 LIFO 括号栈；
2. 彻底保证不变量：
   - 语法校验 (validate_prompt_syntax) 与分词 (tokenize_prompt_spans) 100% 规则恒等；
   - 任何通过校验的文本，tokenize 后拼接 'join(s.text)' 必与原字符串 100% 字节无损恒等；
   - 在 PAREN/BRACKET 内部，正确识别嵌套的 QUOTED / ANGLE / 转义字符，内部闭合符绝不提前截断外层括号；
   - 顶层逗号切分 (split_top_level_tags) 严格保护各类嵌套与转义逗号；
3. 识别六大 SpanType：PLAIN, PAREN, BRACKET, ANGLE, QUOTED, ESCAPED。
"""
from __future__ import annotations

from typing import List, Tuple, Optional
from dataclasses import dataclass

try:
    from .errors import PromptSyntaxError
    from .models import PromptSpan, SpanType
except (ImportError, ValueError):
    from lib.errors import PromptSyntaxError
    from lib.models import PromptSpan, SpanType


@dataclass(frozen=True)
class ParsedTag:
    """单一顶层 Tag 的原文切片与词法元数据。"""
    text: str                     # 原文精确切片 text[start_idx:end_idx]
    raw_slice: str                # 未剥离空白的原始切片 text[raw_start_idx:raw_end_idx]
    start_idx: int                # 边界调整后的起始索引
    end_idx: int                  # 边界调整后的结束索引
    raw_start_idx: int            # 原始起始索引
    raw_end_idx: int              # 原始结束索引
    spans: Tuple[PromptSpan, ...] # 属于该 Tag 的 PromptSpan 元组


@dataclass(frozen=True)
class ParsedPrompt:
    """单次词法流全量扫描结果 (SSOT)。"""
    raw_text: str
    spans: Tuple[PromptSpan, ...]
    tags: Tuple[ParsedTag, ...]
    is_valid: bool = True


def is_protected_fragment(text: str) -> bool:
    """判断片段整体是否为受保护语法（LoRA、双引号、转义或括号）。"""
    s = text.strip()
    if not s:
        return False
    if s.startswith("<") and s.endswith(">"):
        return True
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return True
    if chr(92) in s:
        return True
    if (s.startswith("(") and s.endswith(")")) or (s.startswith("[") and s.endswith("]")):
        return True
    return False


def tokenize_prompt_spans(text: str) -> List[PromptSpan]:
    r"""
    将输入文本切分为连续的 PromptSpan 序列，严格校验语法完整性。
    支持：
    - PLAIN：普通文本
    - ESCAPED：顶层转义字符（如 \,）
    - QUOTED：双引号短语（如 "quoted, phrase"）
    - ANGLE：尖括号结构（如 <lora:model:1.0>）
    - PAREN：圆括号权重（如 (text:1.2)，支持内部嵌套引号、尖括号及转义）
    - BRACKET：方括号调度（如 [text1:text2:10]，支持内部嵌套引号、尖括号及转义）

    不变量：
    1. 任何未闭合或括号错位均抛出 PromptSyntaxError；
    2. "".join(s.text for s in spans) == text 保持 100% 字节级恒等。
    """
    if not text:
        return []

    spans: List[PromptSpan] = []
    idx = 0
    n = len(text)
    current_plain: List[str] = []
    plain_start = -1

    def flush_plain(end_i: int):
        nonlocal plain_start
        if current_plain:
            content = "".join(current_plain)
            spans.append(
                PromptSpan(
                    text=content,
                    span_type=SpanType.PLAIN,
                    start_idx=plain_start,
                    end_idx=end_i,
                    raw_text=content,
                )
            )
            current_plain.clear()
            plain_start = -1

    while idx < n:
        char = text[idx]

        # 1. 顶层反斜杠转义 (尾部裸反斜杠严格 Fail-Closed)
        if char == "\\":
            if idx + 1 >= n:
                flush_plain(idx)
                raise PromptSyntaxError(f"Trailing unescaped backslash at index {idx}")
            flush_plain(idx)
            escaped_seq = text[idx:idx + 2]
            spans.append(
                PromptSpan(
                    text=escaped_seq,
                    span_type=SpanType.ESCAPED,
                    start_idx=idx,
                    end_idx=idx + 2,
                    raw_text=escaped_seq,
                )
            )
            idx += 2
            continue

        # 2. 顶层双引号短语
        if char == '"':
            flush_plain(idx)
            start = idx
            idx += 1
            found = False
            while idx < n:
                if text[idx] == "\\":
                    if idx + 1 >= n:
                        raise PromptSyntaxError("Trailing unescaped backslash inside double quotes")
                    idx += 2
                    continue
                if text[idx] == '"':
                    found = True
                    idx += 1
                    break
                idx += 1
            if not found:
                raise PromptSyntaxError(f"Unclosed double quote starting at index {start}")
            spans.append(
                PromptSpan(
                    text=text[start:idx],
                    span_type=SpanType.QUOTED,
                    start_idx=start,
                    end_idx=idx,
                    raw_text=text[start:idx],
                )
            )
            continue

        # 3. 顶层尖括号 <lora:...>
        if char == "<":
            flush_plain(idx)
            start = idx
            idx += 1
            found = False
            while idx < n:
                if text[idx] == "\\":
                    if idx + 1 >= n:
                        raise PromptSyntaxError("Trailing unescaped backslash inside angle bracket")
                    idx += 2
                    continue
                if text[idx] == "<":
                    raise PromptSyntaxError(f"Nested angle bracket '<' found at index {idx}")
                if text[idx] == ">":
                    found = True
                    idx += 1
                    break
                idx += 1
            if not found:
                raise PromptSyntaxError(f"Unclosed angle bracket starting at index {start}")
            spans.append(
                PromptSpan(
                    text=text[start:idx],
                    span_type=SpanType.ANGLE,
                    start_idx=start,
                    end_idx=idx,
                    raw_text=text[start:idx],
                )
            )
            continue

        # 4. 遇到未配对的闭合尖括号
        if char == ">":
            raise PromptSyntaxError(f"Unmatched closing angle bracket '>' found at index {idx}")

        # 5. 遇到未配对的闭合圆括号或方括号
        if char in (")", "]"):
            raise PromptSyntaxError(f"Unmatched closing bracket '{char}' at index {idx}")

        # 6. 圆括号 '(' 或方括号 '[' 开始的结构
        if char in ("(", "["):
            flush_plain(idx)
            start = idx
            span_type = SpanType.PAREN if char == "(" else SpanType.BRACKET
            stack: List[str] = [char]
            contains_blackbox = False
            idx += 1

            matching = {")": "(", "]": "["}

            while idx < n and stack:
                c = text[idx]

                if c == "\\":
                    if idx + 1 >= n:
                        raise PromptSyntaxError("Trailing unescaped backslash inside brackets")
                    idx += 2
                    continue

                # 括号内部的双引号短语 (黑盒)
                if c == '"':
                    idx += 1
                    quote_closed = False
                    while idx < n:
                        if text[idx] == "\\":
                            if idx + 1 >= n:
                                raise PromptSyntaxError("Trailing unescaped backslash inside quotes within bracket")
                            idx += 2
                            continue
                        if text[idx] == '"':
                            quote_closed = True
                            contains_blackbox = True
                            idx += 1
                            break
                        idx += 1
                    if not quote_closed:
                        raise PromptSyntaxError(f"Unclosed double quote inside bracket starting near index {start}")
                    continue

                # 括号内部的尖括号结构 (LoRA/Embedding 黑盒)
                if c == "<":
                    idx += 1
                    angle_closed = False
                    while idx < n:
                        if text[idx] == "\\":
                            if idx + 1 >= n:
                                raise PromptSyntaxError("Trailing unescaped backslash inside angle within bracket")
                            idx += 2
                            continue
                        if text[idx] == "<":
                            raise PromptSyntaxError(f"Nested angle bracket '<' inside bracket at index {idx}")
                        if text[idx] == ">":
                            angle_closed = True
                            contains_blackbox = True
                            idx += 1
                            break
                        idx += 1
                    if not angle_closed:
                        raise PromptSyntaxError(f"Unclosed angle bracket inside bracket starting near index {start}")
                    continue

                if c in ("(", "["):
                    stack.append(c)
                    idx += 1
                    continue

                if c in (")", "]"):
                    if not stack:
                        raise PromptSyntaxError(f"Unmatched closing bracket '{c}' at index {idx}")
                    top = stack.pop()
                    if top != matching[c]:
                        raise PromptSyntaxError(
                            f"Mismatched closing bracket '{c}' at index {idx}, expected match for '{top}'"
                        )
                    idx += 1
                    continue

                idx += 1

            if stack:
                raise PromptSyntaxError(f"Unclosed opening bracket(s) in prompt starting at index {start}: {''.join(stack)}")

            spans.append(
                PromptSpan(
                    text=text[start:idx],
                    span_type=span_type,
                    contains_blackbox=contains_blackbox,
                    start_idx=start,
                    end_idx=idx,
                    raw_text=text[start:idx],
                )
            )
            continue

        # 7. 普通字符
        if plain_start == -1:
            plain_start = idx
        current_plain.append(char)
        idx += 1

    flush_plain(n)
    return spans


def _split_plain_span(span: PromptSpan) -> List[Tuple[Optional[PromptSpan], bool]]:
    """
    将包含逗号的 PLAIN span 按逗号切分成段落，返回 [(sub_span, is_comma_boundary), ...]。
    is_comma_boundary 为 True 表示此处是一个顶层逗号分隔符。
    保留每个分段精确的 start_idx 与 end_idx 原文索引。
    """
    txt = span.text
    if "," not in txt:
        return [(span, False)]

    results: List[Tuple[Optional[PromptSpan], bool]] = []
    curr_start = span.start_idx
    rel_start = 0

    for i, ch in enumerate(txt):
        if ch == ",":
            if i > rel_start:
                sub_txt = txt[rel_start:i]
                sub_end = curr_start + (i - rel_start)
                results.append((
                    PromptSpan(
                        text=sub_txt,
                        span_type=SpanType.PLAIN,
                        start_idx=curr_start,
                        end_idx=sub_end,
                        raw_text=sub_txt,
                    ),
                    False,
                ))
            results.append((None, True))
            curr_start = curr_start + (i - rel_start) + 1
            rel_start = i + 1

    if rel_start < len(txt):
        sub_txt = txt[rel_start:]
        results.append((
            PromptSpan(
                text=sub_txt,
                span_type=SpanType.PLAIN,
                start_idx=curr_start,
                end_idx=span.end_idx,
                raw_text=sub_txt,
            ),
            False,
        ))

    return results


def parse_prompt(text: str) -> ParsedPrompt:
    """
    单一权威提示词解析入口 (SSOT)：
    单次扫描生成完整的 PromptSpan 序列，校验语法有效性，并精确切分出基于原文索引的顶层 Tag 列表。
    所有语法校验、标签拆分、原子化与预算计算均消费此单次解析结果。
    """
    if not text:
        return ParsedPrompt(raw_text="", spans=(), tags=(), is_valid=True)

    all_spans = tuple(tokenize_prompt_spans(text))
    tags: List[ParsedTag] = []
    curr_tag_spans: List[PromptSpan] = []

    def flush_tag():
        if not curr_tag_spans:
            return

        raw_start = curr_tag_spans[0].start_idx
        raw_end = curr_tag_spans[-1].end_idx
        raw_slice = text[raw_start:raw_end]

        # 1. 仅剥离首部 PLAIN 空白
        while curr_tag_spans and curr_tag_spans[0].span_type == SpanType.PLAIN:
            txt = curr_tag_spans[0].text
            stripped = txt.lstrip(" \r\n\t")
            if not stripped:
                curr_tag_spans.pop(0)
            else:
                leading_ws = len(txt) - len(stripped)
                curr_tag_spans[0] = PromptSpan(
                    text=stripped,
                    span_type=SpanType.PLAIN,
                    start_idx=curr_tag_spans[0].start_idx + leading_ws,
                    end_idx=curr_tag_spans[0].end_idx,
                    raw_text=stripped,
                )
                break

        # 2. 仅剥离尾部 PLAIN 空白
        while curr_tag_spans and curr_tag_spans[-1].span_type == SpanType.PLAIN:
            txt = curr_tag_spans[-1].text
            stripped = txt.rstrip(" \r\n\t")
            if not stripped:
                curr_tag_spans.pop()
            else:
                trailing_ws = len(txt) - len(stripped)
                curr_tag_spans[-1] = PromptSpan(
                    text=stripped,
                    span_type=SpanType.PLAIN,
                    start_idx=curr_tag_spans[-1].start_idx,
                    end_idx=curr_tag_spans[-1].end_idx - trailing_ws,
                    raw_text=stripped,
                )
                break

        if curr_tag_spans:
            start_i = curr_tag_spans[0].start_idx
            end_i = curr_tag_spans[-1].end_idx
            exact_text = text[start_i:end_i]
            if exact_text:
                tags.append(
                    ParsedTag(
                        text=exact_text,
                        raw_slice=raw_slice,
                        start_idx=start_i,
                        end_idx=end_i,
                        raw_start_idx=raw_start,
                        raw_end_idx=raw_end,
                        spans=tuple(curr_tag_spans),
                    )
                )
        curr_tag_spans.clear()

    for sp in all_spans:
        if sp.span_type == SpanType.PLAIN and "," in sp.text:
            chunks = _split_plain_span(sp)
            for sub_span, is_comma in chunks:
                if is_comma:
                    flush_tag()
                elif sub_span is not None and sub_span.text:
                    curr_tag_spans.append(sub_span)
        else:
            curr_tag_spans.append(sp)

    flush_tag()
    return ParsedPrompt(
        raw_text=text,
        spans=all_spans,
        tags=tuple(tags),
        is_valid=True,
    )


def validate_prompt_syntax(text: str) -> None:
    """
    严格验证提示词语法有效性。
    直接消费统一的 parse_prompt，保证校验与分词结果 100% 恒等。
    """
    if not text:
        return
    parse_prompt(text)


def split_top_level_tags(text: str) -> List[str]:
    """
    按顶层未转义逗号拆分提示词文本，严格保护被 (), [], <>, "" 包裹的内部逗号与转义逗号。
    直接消费 parse_prompt 单次解析结果，返回顶层 Tag 原文切片列表。
    """
    if not text or not text.strip():
        return []
    return [t.text for t in parse_prompt(text).tags]
