"""audio 域 L3 兜底：保证任何 query 都有非空返回。

audio 两工具都由 L1 确定性规则覆盖，兜底几乎不会触发；
仅当 L1 未能生成时兜底成 audio_search（原文改写后透传，query 风格与 L1 默认一致）。
"""
from __future__ import annotations

from .rules import _is_play, _norm_query


def fallback(query: str) -> tuple[str, dict]:
    q = _norm_query(query.strip() or "")
    return "audio_search", {"action": "play" if _is_play(q) else "search", "query": q}