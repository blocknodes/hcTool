"""文本归一 key 算法（L2 badcase 与评测共用，务必同源）。

原则：宁紧勿松。归一太松会把不同意图撞进同一 key → 直出错答案（L2 唯一致命风险）。
只做无歧义的机械归一：strip、小写、全角→半角、去所有空白、去标点、去礼貌前缀。
不删有语义的字（不删"不/不要/没"等否定词），避免 正/反 意图撞 key。

配单测锁定 (输入, 期望 key)，防止被无意改动。
"""

from __future__ import annotations

import re

# 纯礼貌前缀（语义为零，删了不影响意图，且不会把不同意图撞进同一 key）
_POLITE = re.compile(r"^(?:请|麻烦|帮我|帮)(?:一下)?")
_FW2HW = {ord(c): ord(c) - 0xFEE0 for c in "！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［］＾＿｀｛｜｝～"}
_FW2HW[ord("　")] = ord(" ")


def to_halfwidth(text: str) -> str:
    return text.translate(_FW2HW)


def strip_punct(text: str) -> str:
    # 去掉所有非 [中文字符/数字/字母] 的字符（含空格、全角符、·、标点）
    return re.sub(r"[^\w一-龥A-Za-z0-9]", "", text)


def strip_polite(text: str) -> str:
    return _POLITE.sub("", text)


def normalize(text: str) -> str:
    s = (text or "").strip().lower()
    s = to_halfwidth(s)
    s = re.sub(r"\s+", "", s)      # 去所有空白（含全角空格）
    s = strip_punct(s)             # 去标点（保留中文字符/数字/字母）
    s = strip_polite(s)            # 去「请/帮我」等无意义前缀
    return s