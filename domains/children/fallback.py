"""少儿(children)域 L3 兜底：fuzzy 是终极安全网。"""
from __future__ import annotations

from . import dsl


def fallback(query: str) -> tuple[str, dict]:
    q = query.strip() or ""
    qn = dsl.build_query_dsl(q)
    if qn:
        tool = dsl.route_tool(q)
        if tool != "educ_fuzzy_search":
            return tool, {"retext": q, "query": qn}
    return "educ_fuzzy_search", {"query": q}