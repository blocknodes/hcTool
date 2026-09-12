"""qa 域 L3 兜底：任何问答 query 保底 fan_knowledge_agent。"""
from __future__ import annotations

from .fanparams import fan_params


def fallback(query: str) -> tuple[str, dict]:
    return ("fan_knowledge_agent", fan_params(query))
