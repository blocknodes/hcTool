"""education 域后处理：原样返回（bench 的 canonical 已做 order-insensitive 归一）。"""
from __future__ import annotations


def normalize(tool: str, params: dict) -> dict:
    return params if isinstance(params, dict) else (params or {})


postprocess = normalize
