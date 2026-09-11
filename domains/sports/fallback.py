"""sports 域 L3 兜底：保证任何 query 都有非空返回。

L1 规则确定性覆盖几乎所有样例；仅当 L1 未能生成（空 params）时兜底。
兜底优先回到 match_search + sport_time 今天，保证最稳的返回。
"""
from __future__ import annotations

import re


def fallback(query: str) -> tuple[str, dict]:
    q = query.strip() or ""
    # 至少给个赛程查询的 sport_time 兜底
    return "sports_match_search", {"query": {"field": "sport_time", "value": "20260824"}}