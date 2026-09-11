"""L3 兜底：保证任何 query 都有非空返回。

semantic：多 `query` 语义表达型 → 直接 fuzzy；否则能干净拼出 DSL → search
（命中全库维度→search_all）；完全结构化不了 → fuzzy。
fuzzy 是终极安全网（param=整句原话，永远可构造）。

关键：不要把"抽象描述/片段/台词/适合…看"这类只能整句模糊检索的 query，
仅因能挤出 category 就误判成结构化 search。fuzzy 优先判断先于 search。

与 L1 主动判 fuzzy 的关键区分：L3 出的是 source=fallback（"没稳才落"的网），
L1 主动判 fuzzy 是 source=general_rule。两者必须区分，兜底率才有意义的健康指标。
"""

from __future__ import annotations

import re

from . import dsl
from .rules import _FUZZY_HINT, _FUZZY_STRONG  # 复用 fuzzy 信号

# L3 里应 fuzzy 的表达型提示（无具体槽位可拆，只能整句检索）：
# 描述/场景/适合/推荐…看 /出自 /哪部 /什么 (电影剧) /剧名…
_FUZZY_EXPR = re.compile(
    r"适合.{0,14}(?:看|电影|剧|片)|推荐.{0,8}(?:电影|剧|看)|有没有.{0,8}(?:电影|剧|看)|"
    r"出自|哪部|什么电影|哪个电视剧|是什么|是哪|叫什么|哪些|找几部|找一部|"
    r"什么心情|适合.{0,10}的吧|在哪(?:部|一)|哪集|怎样的"
)
_FUZZY_TOTAL = re.compile(
    "(" + _FUZZY_STRONG.pattern + ")|(" + _FUZZY_HINT.pattern + ")|(" + _FUZZY_EXPR.pattern + ")"
)


def fallback(query: str) -> tuple[str, dict]:
    """语义模糊先判（表达型/片段/台词/推荐…看/出自/哪一部，只能整句检索，
    即使能挤出 category 也归 fuzzy）→ 剩余结构化 query 走三级覆盖判定：
    search 命中 → search；仅 search_all 命中 → search_all；
    两者都不覆盖、或槽位值不在枚举范围内 → fuzzy。"""
    if _FUZZY_TOTAL.search(query):
        return "vod_fuzzy_search", {"query": query}
    tool = dsl.route_tool(query)
    if tool == "vod_fuzzy_search":
        return "vod_fuzzy_search", {"query": query}
    d = dsl.build_search_dsl(query)
    return tool, (d or {"query": query})