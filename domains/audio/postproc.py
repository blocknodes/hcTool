"""audio 域参数后处理：规范化 action/category/time 生成结果。

audio 工具无嵌套 DSL，仅需保证 params 为干净 dict；rules 已确定生成，故这里原样返回。
"""
from __future__ import annotations


def normalize(tool: str, params: dict) -> dict:
    if not isinstance(params, dict):
        return params or {}
    return params


postprocess = normalize
