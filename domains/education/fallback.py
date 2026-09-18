"""education 域 L3 兜底：落到 edu_fuzzy_search（retext=原话，恒可构造）。"""
from __future__ import annotations

from .rules import _is_structured


def fallback(query: str) -> tuple[str, dict]:
    q = query.strip() or ""
    if _is_structured(q):
        from .rules import build_dsl
        d = build_dsl(q)
        if d:
            return "edu_search", d
    return "edu_fuzzy_search", {"retext": q}
